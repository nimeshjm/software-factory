import os
from types import SimpleNamespace

import pytest

from factory.guards import GuardLog, ToolGuard
from factory.guards.tool_guard import (BASELINE_PATH_RULES, BASELINE_SHELL_RULES, NEVER_EXPORT_PREFIXES, check_shell,
                                       evaluate)
from factory.schemas.target_profile import TargetProfile
from factory.tools import shell
from factory.tools.workspace import clean_env

HOSTS = {"api.github.com"}


@pytest.mark.parametrize("cmd,rule", [
    ("git push origin main", "git_push"),
    ("git -C . push", "git_push"),
    ("gh pr create", "gh_cli"),
    ("echo hi && gh issue list", "gh_cli"),
    ("curl https://evil.example.com/x", "network_host"),
    ("wget evil.example.com/a", "network_host"),
    ("rm -rf /", "rm_rf_outside_workspace"),
    ("rm -rf ~/stuff", "rm_rf_outside_workspace"),
    ("rm -rf $HOME", "rm_rf_outside_workspace"),
    ("rm -fr ../other", "rm_rf_outside_workspace"),
    ("sudo ls", "sudo"),
    ("sleep 100 &", "background"),
    ("nohup make serve", "background"),
    ("echo x > .github/workflows/a.yml", "protected_path_write"),
])
def test_shell_blocked(ws, cmd, rule):
    got = check_shell(cmd, ws, HOSTS)
    assert got and got[0] == rule, got


@pytest.mark.parametrize("cmd", [
    "make test", "pytest -q", "git status", "git diff", "ls -la src",
    "rm -rf build", "rm -rf dist/out", "curl -s http://localhost:8787/health",
    "curl -H 'x: y' https://api.github.com/repos/o/r", "pytest 2>&1 | tail -5",
    "grep -r 'a&b' src", "cat .gitignore", "cp a.txt .gitignore",
    # generic baseline no longer carries target-specific rules
    "npx wrangler deploy", "EVAL_LIVE=1 npm test", "npm run deploy",
])
def test_shell_allowed(ws, cmd):
    assert check_shell(cmd, ws, HOSTS) is None, cmd


def test_registry_host_not_in_baseline(ws):
    assert check_shell("curl https://registry.npmjs.org/x", ws, HOSTS)[0] == "network_host"
    assert check_shell("curl https://registry.npmjs.org/x", ws, HOSTS | {"registry.npmjs.org"}) is None


def test_allowed_hosts_default_from_factory_config(ws):
    assert check_shell("curl -s https://api.github.com/x", ws) is None
    assert check_shell("curl -s https://evil.example.com/x", ws)[0] == "network_host"


def test_path_outside_and_dotdot(ws):
    assert evaluate("read_file", {"path": "../x"})[0] == "path_outside_workspace"
    assert evaluate("read_file", {"path": "/etc/passwd"})[0] == "path_outside_workspace"
    assert evaluate("search_files", {"pattern": "x", "path_glob": "../**"})[0] == "path_outside_workspace"


def test_symlink_escape(ws, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    (ws / "link").symlink_to(outside)
    assert evaluate("read_file", {"path": "link"})[0] == "path_outside_workspace"


def test_protected_paths(ws):
    (ws / ".github").mkdir()
    assert evaluate("edit_file", {"path": ".github/workflows/ci.yml", "command": "create", "file_text": "x"})[0] == "protected_path"
    assert evaluate("edit_file", {"path": "factory.yml", "command": "create", "file_text": "x"})[0] == "protected_path"
    assert evaluate("read_file", {"path": ".git/config"})[0] == "protected_path"
    assert evaluate("read_file", {"path": ".github/workflows/ci.yml"}) is None
    assert evaluate("read_file", {"path": "factory.yml"}) is None


def test_allow_path(ws):
    (ws / "src").mkdir()
    assert evaluate("edit_file", {"path": "src/a.py", "command": "create", "file_text": "x"}) is None
    assert evaluate("list_files", {"path": "."}) is None
    assert evaluate("unknown_tool", {}) is None


def test_hook_sets_cancel_and_logs(ws):
    log = GuardLog()
    ev = SimpleNamespace(tool_use={"name": "shell_in_workspace", "input": {"command": "git push"}}, cancel_tool=False)
    ToolGuard(log, allowed_hosts=HOSTS).before_tool(ev)
    assert ev.cancel_tool.startswith("[tool_guard] Blocked git_push:")
    assert log.events[0]["guard"] == "tool_guard" and log.events[0]["rule"] == "git_push"
    ok = SimpleNamespace(tool_use={"name": "shell_in_workspace", "input": {"command": "make test"}}, cancel_tool=False)
    ToolGuard(log, allowed_hosts=HOSTS).before_tool(ok)
    assert ok.cancel_tool is False


def _hook(guard, name, **inp):
    ev = SimpleNamespace(tool_use={"name": name, "input": inp}, cancel_tool=False)
    guard.before_tool(ev)
    return ev.cancel_tool


def test_profile_rules_block_and_default_allows(ws):
    prof = TargetProfile.model_validate({"protected": {"commands": [r"\bwrangler\b"], "env_markers": ["EVAL_LIVE"],
                                                       "paths": ["config/prod.*"]}})
    strict, lax = ToolGuard(GuardLog(), prof, HOSTS), ToolGuard(GuardLog(), TargetProfile(), HOSTS)
    for cmd in ("npx wrangler deploy", "node_modules/.bin/wrangler dev"):
        assert _hook(strict, "shell_in_workspace", command=cmd).startswith("[tool_guard] Blocked profile_command:")
        assert _hook(lax, "shell_in_workspace", command=cmd) is False
    assert _hook(strict, "shell_in_workspace", command="EVAL_LIVE=1 npm test").startswith(
        "[tool_guard] Blocked profile_env_marker:")
    assert _hook(lax, "shell_in_workspace", command="EVAL_LIVE=1 npm test") is False
    assert _hook(strict, "shell_in_workspace", command="EVAL_LIVE_OTHER=1 make") is False  # whole-name match
    (ws / "config").mkdir()
    assert _hook(strict, "edit_file", path="config/prod.json", command="create", file_text="x").startswith(
        "[tool_guard] Blocked profile_path:")
    assert _hook(strict, "read_file", path="config/prod.json") is False
    assert _hook(lax, "edit_file", path="config/prod.json", command="create", file_text="x") is False


def test_profile_cannot_remove_baseline(ws):
    prof = TargetProfile.model_validate({"protected": {"commands": ["x"]}, "toolchain": {"env_prefixes": ["GH_"]}})
    g = ToolGuard(GuardLog(), prof, HOSTS)
    assert _hook(g, "shell_in_workspace", command="git push").startswith("[tool_guard] Blocked git_push")
    assert _hook(g, "shell_in_workspace", command="gh pr list").startswith("[tool_guard] Blocked gh_cli")
    assert _hook(g, "edit_file", path="factory.yml", command="create", file_text="x").startswith(
        "[tool_guard] Blocked protected_path")
    assert {r for r, *_ in BASELINE_SHELL_RULES} >= {"git_push", "gh_cli", "sudo"}
    assert {g_ for _, g_, *_ in BASELINE_PATH_RULES} >= {".git/**", ".github/**", "factory.yml"}


def test_clean_env_never_exports(monkeypatch, set_prof):
    for k in ("GEMINI_API_KEY", "OTEL_EXPORTER_X", "GITHUB_TOKEN", "FACTORY_RUN_ID", "HONEYCOMB_QUERY_KEY",
              "APP_MODE", "NODE_ENV"):
        monkeypatch.setenv(k, "v")
    base = clean_env()
    assert "APP_MODE" not in base and "NODE_ENV" not in base and "PATH" in base and base["CI"]
    set_prof(toolchain={"env_prefixes": ["GEMINI", "OTEL_", "GITHUB_", "NODE_", "APP_"]})
    env = clean_env()
    assert env["NODE_ENV"] == "v" and env["APP_MODE"] == "v"
    assert not [k for k in env if k.startswith(NEVER_EXPORT_PREFIXES)]
    assert "GEMINI_API_KEY" in os.environ


def test_shell_tool_cannot_see_never_export(ws, monkeypatch, set_prof):
    monkeypatch.setenv("GEMINI_API_KEY", "topsecret")
    monkeypatch.setenv("OTEL_X", "otelsecret")
    set_prof(toolchain={"env_prefixes": ["GEMINI", "OTEL_"]})
    out = shell.shell_in_workspace("echo [$GEMINI_API_KEY][$OTEL_X]")
    assert "topsecret" not in out and "otelsecret" not in out and "[][]" in out
