"""ToolGuard: deny-list for shell commands and path rules for file tools."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from factory.config import load_config
from factory.policy.globs import match_glob
from factory.schemas.target_profile import TargetProfile
from factory.tools.workspace import PathEscape, resolve_in_workspace, workspace_root

from .events import GuardLog

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
PATH_TOOLS = {"read_file", "list_files", "search_files", "edit_file"}
CURL_VALUE_FLAGS = {"-H", "--header", "-d", "--data", "--data-raw", "--data-binary", "-X", "--request", "-o",
                    "--output", "-u", "--user", "-A", "--user-agent", "-e", "--referer", "-b", "--cookie",
                    "-F", "--form", "-O", "-P", "--post-data", "--header=", "-T"}

# Baseline rules are compiled in; a target profile can only add to them.
# (rule, regex, detail)
BASELINE_SHELL_RULES: tuple[tuple[str, str, str], ...] = (
    ("git_push", r"\bgit\b[^;&|]*\bpush\b", "agents never push; the pipeline does"),
    ("gh_cli", r"(?:^|[\s;&|(`])gh(?:\s|$)", "the GitHub CLI is not available to agents"),
    ("sudo", r"\bsudo\b", "sudo is not allowed"),
)
# (rule, glob, tools the rule applies to, detail)
ALL_PATH_TOOLS = frozenset(PATH_TOOLS)
BASELINE_PATH_RULES: tuple[tuple[str, str, frozenset[str], str], ...] = (
    ("protected_path", ".git", ALL_PATH_TOOLS, ".git/** is off limits"),
    ("protected_path", ".git/**", ALL_PATH_TOOLS, ".git/** is off limits"),
    ("protected_path", ".github/**", frozenset({"edit_file"}), ".github/** cannot be edited"),
    ("protected_path", "factory.yml", frozenset({"edit_file"}), "factory.yml cannot be edited"),
)
# Env names starting with any of these never reach tool subprocesses, whatever the profile says.
NEVER_EXPORT_PREFIXES: tuple[str, ...] = ("GEMINI", "GITHUB_", "GH_", "OTEL_", "HONEYCOMB", "FACTORY_", "ACTIONS_",
                                          "RUNNER_")


@dataclass
class Rules:
    """Baseline rules plus the profile's additions, compiled once."""

    shell: list[tuple[str, re.Pattern[str], str]] = field(default_factory=list)
    path_globs: list[str] = field(default_factory=list)

    @classmethod
    def compile(cls, profile: TargetProfile | None = None) -> Rules:
        r = cls([(n, re.compile(rx), d) for n, rx, d in BASELINE_SHELL_RULES])
        if profile is not None:
            for rx in profile.protected.commands:
                r.shell.append(("profile_command", re.compile(rx), f"command matches protected pattern {rx!r}"))
            for m in profile.protected.env_markers:
                r.shell.append(("profile_env_marker", re.compile(rf"(?<![A-Za-z0-9_]){re.escape(m)}(?![A-Za-z0-9_])"),
                                f"{m} must never appear in a command"))
            r.path_globs = list(profile.protected.paths)
        return r


def _hosts_for(allowed_hosts) -> frozenset[str]:
    if allowed_hosts is None:
        allowed_hosts = load_config().get("allowed_hosts", [])
    return frozenset(h.lower() for h in allowed_hosts) | LOCAL_HOSTS


def _strip_quotes(cmd: str) -> str:
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", cmd)


def _tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def _curl_hosts(cmd: str) -> list[str]:
    hosts: list[str] = []
    toks = _tokens(cmd)
    for i, t in enumerate(toks):
        if t not in ("curl", "wget") and not t.endswith(("/curl", "/wget")):
            continue
        skip = False
        for a in toks[i + 1:]:
            if a in (";", "&&", "||", "|"):
                break
            if skip:
                skip = False
                continue
            if a in CURL_VALUE_FLAGS:
                skip = True
                continue
            if a.startswith("-"):
                continue
            hosts.append((urlparse(a if "://" in a else "//" + a).hostname or "").lower())
        if not hosts:
            hosts.append("")  # curl with no parsable host: treat as non-allowlisted
    return hosts


def _rm_targets(cmd: str) -> list[str] | None:
    """Return path args of rm -r/-f invocations (None if no recursive rm)."""
    toks = _tokens(cmd)
    found: list[str] = []
    hit = False
    for i, t in enumerate(toks):
        if t != "rm" and not t.endswith("/rm"):
            continue
        flags = ""
        for a in toks[i + 1:]:
            if a in (";", "&&", "||", "|"):
                break
            if a.startswith("--"):
                flags += "r" if a == "--recursive" else ""
            elif a.startswith("-"):
                flags += a[1:]
            else:
                found.append(a)
        if "r" in flags.lower():
            hit = True
    return found if hit else None


def check_shell(command: str, workspace: Path | None = None, allowed_hosts=None,
                profile: TargetProfile | None = None, rules: Rules | None = None) -> tuple[str, str] | None:
    """Return (rule, detail) when the command must be blocked, else None."""
    c = command or ""
    rules = rules or Rules.compile(profile)
    for name, rx, detail in rules.shell:
        if name.startswith("profile_"):
            continue
        if rx.search(c):
            return name, detail
    if re.search(r"\b(?:curl|wget)\b", c):
        allowed = _hosts_for(allowed_hosts)
        bad = [h for h in _curl_hosts(c) if h not in allowed]
        if bad:
            return "network_host", f"host not in allowlist: {bad[0] or '(unparsed)'}"
    rm = _rm_targets(c)
    if rm is not None:
        ws = (workspace or workspace_root()).resolve()
        for target in rm:
            if "$" in target or "`" in target or target.startswith("~"):
                return "rm_rf_outside_workspace", f"unresolvable rm target: {target}"
            p = Path(target)
            p = p if p.is_absolute() else ws / p
            try:
                p.resolve().relative_to(ws)
            except ValueError:
                return "rm_rf_outside_workspace", f"rm -r target outside workspace: {target}"
    if re.search(r"\b(?:nohup|setsid|disown)\b", c):
        return "background", "background processes are not allowed"
    if re.search(r"(?<![&>|])&(?![&>])", _strip_quotes(c)):
        return "background", "background '&' is not allowed"
    if re.search(r"(?:>|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b)[^;&|]*\.(?:github|git)(?![\w.-])", c):
        return "protected_path_write", "writes to .github/ or .git/ are not allowed"
    for name, rx, detail in rules.shell:
        if name.startswith("profile_") and rx.search(c):
            return name, detail
    return None


def _norm_rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def check_path_tool(name: str, tool_input: dict, workspace: Path | None = None,
                    profile: TargetProfile | None = None, rules: Rules | None = None) -> tuple[str, str] | None:
    """Return (rule, detail) when a path tool call must be blocked, else None."""
    rules = rules or Rules.compile(profile)
    ws = (workspace or workspace_root()).resolve()
    paths: list[str] = []
    if name in ("read_file", "list_files", "edit_file"):
        paths.append(str(tool_input.get("path", "." if name == "list_files" else "")))
    elif name == "search_files":
        g = str(tool_input.get("path_glob", "**/*"))
        if ".." in Path(g).parts or Path(g).is_absolute():
            return "path_outside_workspace", f"path_glob must be relative without '..': {g}"
        paths.append(".")
    resolved: Path | None = None
    for raw in paths:
        try:
            resolved = resolve_in_workspace(raw, ws)
        except PathEscape as e:
            return "path_outside_workspace", str(e)
    if resolved is None:
        return None
    rel = _norm_rel(resolved, ws) if resolved != ws else "."
    for rule, glob, tools, detail in BASELINE_PATH_RULES:
        if name in tools and match_glob(rel, glob):
            return rule, f"{detail}: {rel}"
    if name == "edit_file":
        for glob in rules.path_globs:
            if match_glob(rel, glob):
                return "profile_path", f"{rel} is protected by the target profile ({glob})"
    return None


def evaluate(name: str, tool_input: dict, workspace: Path | None = None, allowed_hosts=None,
             profile: TargetProfile | None = None, rules: Rules | None = None) -> tuple[str, str] | None:
    rules = rules or Rules.compile(profile)
    if name == "shell_in_workspace":
        return check_shell(str(tool_input.get("command", "")), workspace, allowed_hosts, rules=rules)
    if name in PATH_TOOLS:
        return check_path_tool(name, tool_input, workspace, rules=rules)
    return None


class ToolGuard(HookProvider):
    def __init__(self, log: GuardLog, profile: TargetProfile | None = None, allowed_hosts=None) -> None:
        self.log = log
        self.profile = profile or TargetProfile()
        self.rules = Rules.compile(self.profile)
        self.allowed_hosts = _hosts_for(allowed_hosts)

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:  # noqa: ANN003
        registry.add_callback(BeforeToolCallEvent, self.before_tool)

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name", "")
        verdict = evaluate(name, event.tool_use.get("input") or {}, allowed_hosts=self.allowed_hosts,
                           rules=self.rules)
        if verdict:
            rule, detail = verdict
            event.cancel_tool = f"[tool_guard] Blocked {rule}: {detail}"
            self.log.record("tool_guard", rule, f"{name}: {detail}")
