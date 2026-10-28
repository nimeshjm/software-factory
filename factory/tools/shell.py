"""Shell, checks and git tools running inside WORKSPACE with a reduced environment."""

from __future__ import annotations

import subprocess

from strands import tool

from .profile import get_profile
from .workspace import clean_env, workspace_root

MAX_OUT = 20_000
TRAILER = "Co-Authored-By: software-factory <factory@users.noreply.github.com>"


def _run(args: list[str] | str, *, shell: bool, timeout: float) -> tuple[int, str]:
    cwd = workspace_root()
    try:
        proc = subprocess.run(
            ["sh", "-c", args] if shell else args,  # type: ignore[list-item]
            cwd=cwd,
            env=clean_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"")
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return 124, f"{out}\n[timed out after {timeout}s]"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _tail(text: str, lines: int) -> str:
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def _clip(text: str) -> str:
    if len(text) <= MAX_OUT:
        return text
    h = MAX_OUT // 2
    return text[:h] + f"\n[... {len(text) - MAX_OUT} chars omitted ...]\n" + text[-h:]


@tool
def shell_in_workspace(command: str, timeout: int = 120) -> str:
    """Run a shell command (sh -c) in the workspace. Pushes, background jobs and off-allowlist network access are blocked.

    Args:
        command: The command line to run.
        timeout: Timeout in seconds (default 120).
    """
    timeout = max(1, min(int(timeout), 600))
    code, out = _run(command, shell=True, timeout=timeout)
    return f"exit_code={code}\n{_clip(out)}"


@tool
def run_checks() -> str:
    """Run the target's configured checks (as defined in its factory.yml) in order and summarise the result."""
    steps = [(c.name, c.run) for c in get_profile().toolchain.checks]
    if not steps:
        return "run_checks: no checks configured in factory.yml"
    summary: list[str] = []
    for name, cmd in steps:
        code, out = _run(cmd, shell=True, timeout=600)
        if code == 0:
            summary.append(f"{name}: PASS (exit 0)")
            continue
        summary.append(f"{name}: FAIL (exit {code})")
        summary.append(_tail(out, 60))
        summary.append("(stopped at first failing step)")
        return "checks: FAIL\n" + "\n".join(summary)
    return "checks: PASS\n" + "\n".join(summary)


@tool
def git_commit(message: str) -> str:
    """Commit all changes in the workspace on the current branch. Never pushes.

    Args:
        message: Commit message subject and optional body.
    """
    msg = message.strip()
    if not msg:
        return "error: empty commit message"
    full = f"{msg}\n\n{TRAILER}\n"
    code, out = _run(["git", "add", "-A"], shell=False, timeout=60)
    if code != 0:
        return f"error: git add failed\n{_tail(out, 20)}"
    code, out = _run(["git", "status", "--porcelain"], shell=False, timeout=60)
    if not out.strip():
        return "nothing to commit"
    code, out = _run(
        [
            "git",
            "-c", "user.name=software-factory",
            "-c", "user.email=factory@users.noreply.github.com",
            "commit", "-m", full,
        ],
        shell=False,
        timeout=60,
    )
    if code != 0:
        return f"error: git commit failed\n{_tail(out, 20)}"
    _, sha = _run(["git", "rev-parse", "--short", "HEAD"], shell=False, timeout=30)
    _, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], shell=False, timeout=30)
    return f"committed {sha.strip()} on {branch.strip()}"
