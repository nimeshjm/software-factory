"""scripts/lib.sh gh_retry / create_pr: transient GitHub failures are retried, others are not."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A stub gh. Behaviour comes from the environment:
#   STUB_FAILS: how many leading calls of the watched subcommand fail; STUB_ERR: their stderr text;
#   STUB_WATCH: "<verb> <noun>" to watch (e.g. "pr create"); STUB_LIST_PR: number `gh pr list` reports.
# Every call is appended to $STUB_LOG.
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG"
if [ "$1 $2" = "pr list" ]; then printf '%s' "${STUB_LIST_PR:-}"; exit 0; fi
if [ "$1 $2" = "$STUB_WATCH" ]; then
  n=$(grep -c "^$STUB_WATCH" "$STUB_LOG")
  if [ "$n" -le "${STUB_FAILS:-0}" ]; then printf '%s\\n' "$STUB_ERR" >&2; exit 1; fi
  if [ "$1 $2" = "pr create" ]; then echo "https://github.com/o/r/pull/42"; fi
  exit 0
fi
exit 0
"""


def _run(tmp_path, snippet, **env):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("")
    full = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "GH_RETRY_BASE": "0",
        **env,
    }
    p = subprocess.run(["bash", "-c", f"source scripts/lib.sh; {snippet}"], cwd=ROOT, env=full,
                       capture_output=True, text=True)
    return p, log.read_text().splitlines()


def test_retries_5xx_then_succeeds(tmp_path):
    p, calls = _run(tmp_path, 'gh_retry gh issue edit 7 --add-label x',
                    STUB_WATCH="issue edit", STUB_FAILS="2", STUB_ERR="HTTP 502: Bad Gateway")
    assert p.returncode == 0, p.stderr
    assert len(calls) == 3
    assert p.stderr.count("::warning::") == 2


def test_does_not_retry_4xx(tmp_path):
    p, calls = _run(tmp_path, 'gh_retry gh issue edit 7 --add-label x',
                    STUB_WATCH="issue edit", STUB_FAILS="5", STUB_ERR="HTTP 422: Validation Failed")
    assert p.returncode == 1
    assert len(calls) == 1
    assert "422" in p.stderr


def test_gives_up_after_max(tmp_path):
    p, calls = _run(tmp_path, 'gh_retry gh issue edit 7 --add-label x',
                    STUB_WATCH="issue edit", STUB_FAILS="9", STUB_ERR="HTTP 504: 504 Gateway Timeout",
                    GH_RETRY_MAX="3")
    assert p.returncode == 1
    assert len(calls) == 3
    assert "504" in p.stderr


def test_create_pr_uses_pr_created_despite_504(tmp_path):
    p, calls = _run(tmp_path, 'create_pr fix/1-x --title t',
                    STUB_WATCH="pr create", STUB_FAILS="9", STUB_ERR="HTTP 504: 504 Gateway Timeout",
                    STUB_LIST_PR="17")
    assert p.returncode == 0, p.stderr
    assert p.stdout == "17"
    assert [c.split()[1] for c in calls] == ["create", "list"]
    assert "PR #17 is open for fix/1-x" in p.stderr


def test_create_pr_retries_then_returns_number(tmp_path):
    p, calls = _run(tmp_path, 'create_pr fix/1-x --title t',
                    STUB_WATCH="pr create", STUB_FAILS="1", STUB_ERR="Post \"https://api.github.com/graphql\": connection reset by peer")
    assert p.returncode == 0, p.stderr
    assert p.stdout == "42"
    assert [c.split()[1] for c in calls] == ["create", "list", "create"]


def test_create_pr_gives_up_and_names_branch(tmp_path):
    p, calls = _run(tmp_path, 'create_pr fix/1-x --title t',
                    STUB_WATCH="pr create", STUB_FAILS="9", STUB_ERR="HTTP 503: Service Unavailable",
                    GH_RETRY_MAX="2")
    assert p.returncode == 1
    assert calls.count("pr create --title t") == 2
    assert "origin/fix/1-x" in p.stderr


def test_create_pr_non_transient_fails_at_once(tmp_path):
    p, calls = _run(tmp_path, 'create_pr fix/1-x --title t',
                    STUB_WATCH="pr create", STUB_FAILS="9", STUB_ERR="GraphQL: No commits between main and fix/1-x (createPullRequest)")
    assert p.returncode == 1
    assert calls.count("pr create --title t") == 1
