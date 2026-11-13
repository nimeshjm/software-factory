"""scripts/salvage-run.sh: a failed implement phase is saved on a backup branch, the label reset and the failure commented."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A stub gh. STUB_LABELS (newline separated) answers `issue view`; every call is appended to $STUB_LOG and
# the body file of an `issue comment` is copied to $STUB_COMMENT.
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG"
if [ "$1 $2" = "issue view" ]; then printf '%s\\n' "$STUB_LABELS"; exit 0; fi
if [ "$1 $2" = "issue comment" ] || [ "$1 $2" = "pr comment" ]; then
  while [ $# -gt 0 ]; do
    if [ "$1" = "--body-file" ]; then cp "$2" "$STUB_COMMENT"; fi
    shift
  done
fi
exit 0
"""


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def phase(root, name, **result):
    d = root / "out" / "phases" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps(result))


@pytest.fixture
def env(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    git("checkout", "-q", "-b", "main", cwd=work)
    (work / "a.txt").write_text("a\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("checkout", "-q", "-b", "feat/125-x", cwd=work)

    phase(tmp_path, "research", success=True, stop_reason="end_turn")
    phase(tmp_path, "plan", success=True, stop_reason="end_turn")
    phase(tmp_path, "implement", success=False, stop_reason="limit_total_tokens",
          error="stop_reason=limit_total_tokens")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    cfg = json.loads((ROOT / "factory.config.json").read_text())
    cfg["target"]["default_branch"] = "main"
    (tmp_path / "factory.config.json").write_text(json.dumps(cfg))
    return {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_COMMENT": str(tmp_path / "comment.md"),
        "STUB_LABELS": "status:in-progress\nfactory:accepted",
        "GH_RETRY_BASE": "0",
        "GH_TOKEN": "x",
        "CONFIG": str(tmp_path / "factory.config.json"),
        "TARGET_REPO": "o/r",
        "OUT_DIR": str(tmp_path / "out"),
        "WORK_DIR": str(work),
        "RUN_ID": "run-9",
        "ISSUE": "125",
        "AGENT": "builder",
        "RUN_URL": "https://example.test/run/9",
    }


def salvage(tmp_path, env, **over):
    (tmp_path / "calls.log").touch()
    p = subprocess.run([str(ROOT / "scripts/salvage-run.sh")], cwd=ROOT, env={**env, **over},
                       capture_output=True, text=True)
    return p, (tmp_path / "calls.log").read_text().splitlines()


def backups(tmp_path):
    out = subprocess.run(["git", "branch", "--list", "backup/*"], cwd=tmp_path / "origin.git",
                         capture_output=True, text=True, check=True).stdout
    return sorted(line.strip() for line in out.splitlines())


def test_saves_uncommitted_work_resets_label_and_comments(tmp_path, env):
    (tmp_path / "work" / "b.txt").write_text("wip\n")
    p, calls = salvage(tmp_path, env)
    assert p.returncode == 0, p.stderr
    assert backups(tmp_path) == ["backup/125-attempt1-limit_total_tokens"]
    msg = subprocess.run(["git", "log", "-1", "--format=%B", "backup/125-attempt1-limit_total_tokens"],
                         cwd=tmp_path / "origin.git", capture_output=True, text=True).stdout
    assert msg.startswith("WIP: salvage of run-9 (limit_total_tokens)")
    assert "Co-Authored-By:" in msg
    assert (tmp_path / "out" / "salvage.txt").read_text().strip() == "backup/125-attempt1-limit_total_tokens"
    assert "salvaged: backup/125-attempt1-limit_total_tokens" in p.stderr
    assert any("issue edit 125" in c and "--add-label status:backlog" in c
               and "--remove-label status:in-progress" in c for c in calls)
    body = (tmp_path / "comment.md").read_text()
    assert body.startswith("<!-- factory:failure -->")
    assert "backup/125-attempt1-limit_total_tokens" in body and "run-9" in body
    assert "failed in implement (limit_total_tokens)" in body
    assert "_Run: https://example.test/run/9_" in body
    assert "GH_TOKEN" not in p.stderr


def test_second_attempt_increments(tmp_path, env):
    (tmp_path / "work" / "b.txt").write_text("one\n")
    salvage(tmp_path, env)
    (tmp_path / "work" / "c.txt").write_text("two\n")
    p, _ = salvage(tmp_path, env)
    assert p.returncode == 0, p.stderr
    assert backups(tmp_path) == ["backup/125-attempt1-limit_total_tokens",
                                 "backup/125-attempt2-limit_total_tokens"]


def test_nothing_to_save(tmp_path, env):
    p, calls = salvage(tmp_path, env, STUB_LABELS="status:plan-review")
    assert p.returncode == 0, p.stderr
    assert backups(tmp_path) == []
    assert "salvage: nothing to save" in p.stderr
    assert not (tmp_path / "out" / "salvage.txt").exists()
    assert not any("issue edit" in c for c in calls)
    body = (tmp_path / "comment.md").read_text()
    assert "<!-- factory:failure -->" in body and "Labels left as they were." in body
    assert "Work saved" not in body


def test_failed_plan_touches_no_git(tmp_path, env):
    (tmp_path / "out" / "phases" / "implement" / "result.json").unlink()
    phase(tmp_path, "plan", success=False, stop_reason="timeout", error="deadline")
    (tmp_path / "work" / "b.txt").write_text("wip\n")
    p, _ = salvage(tmp_path, env)
    assert p.returncode == 0, p.stderr
    assert backups(tmp_path) == []
    status = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path / "work",
                            capture_output=True, text=True).stdout
    assert "b.txt" in status
    assert "failed in plan (timeout): deadline." in (tmp_path / "comment.md").read_text()


def test_push_failure_is_a_warning_not_a_failure(tmp_path, env):
    (tmp_path / "work" / "b.txt").write_text("wip\n")
    git("remote", "set-url", "--push", "origin", str(tmp_path / "missing.git"), cwd=tmp_path / "work")
    p, _ = salvage(tmp_path, env)
    assert p.returncode == 0
    assert "::warning::" in p.stderr
    assert not (tmp_path / "out" / "salvage.txt").exists()
    assert "Work saved" not in (tmp_path / "comment.md").read_text()


def test_failed_revise_swaps_pr_labels_and_comments_on_the_pr(tmp_path, env):
    out = tmp_path / "out" / "phases"
    for d in out.iterdir():
        (d / "result.json").unlink()
    phase(tmp_path, "revise", success=False, stop_reason="timeout", error="deadline")
    (tmp_path / "work" / "b.txt").write_text("half done\n")
    p, calls = salvage(tmp_path, env, PR="7")
    assert p.returncode == 0, p.stderr
    assert backups(tmp_path) == []
    assert not (tmp_path / "out" / "salvage.txt").exists()
    assert any("pr edit 7" in c and "--add-label revise:attempted,needs-human" in c
               and "--remove-label revise:requested" in c for c in calls)
    assert not any(c.startswith("issue ") for c in calls)
    assert any(c.startswith("pr comment 7") for c in calls)
    body = (tmp_path / "comment.md").read_text()
    assert body.startswith("<!-- factory:failure -->")
    assert "failed in revise (timeout): deadline." in body
    assert "No work was pushed to the PR branch." in body
