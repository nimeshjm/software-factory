"""scripts/apply-proposal.sh builder revise: push to the PR head, swap the labels, ready the PR, comment."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A stub gh: every call is appended to $STUB_LOG; `pr view` answers isDraft and `pr comment` copies its body file.
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG"
if [ "$1 $2" = "pr view" ]; then echo true; exit 0; fi
if [ "$1 $2" = "pr comment" ]; then
  while [ $# -gt 0 ]; do
    if [ "$1" = "--body-file" ]; then cp "$2" "$STUB_COMMENT"; fi
    shift
  done
fi
exit 0
"""


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def make_env(tmp_path, commits_ahead):
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
    git("push", "-q", "origin", "feat/125-x", cwd=work)
    for i in range(commits_ahead):
        (work / f"r{i}.txt").write_text("fix\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", f"revise {i}", cwd=work)

    inputs, out = tmp_path / "inputs", tmp_path / "out"
    inputs.mkdir()
    out.mkdir()
    (inputs / "factory.json").write_text(json.dumps({
        "dark_paths": ["docs/**"], "toolchain": {"checks": [{"name": "test", "run": "true"}]}}))
    (inputs / "pr.json").write_text(json.dumps({
        "number": 7, "headRefName": "feat/125-x", "isDraft": True, "author": "app/software-factory-agent-bot"}))
    (out / "proposal.json").write_text(json.dumps({
        "branch": "feat/125-x", "title": "t", "body": "Fixed the thing, ping @someone.", "files": ["r0.txt"],
        "checks_passed": True}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    cfg = json.loads((ROOT / "factory.config.json").read_text())
    cfg["target"]["default_branch"] = "main"
    (tmp_path / "factory.config.json").write_text(json.dumps(cfg))
    (tmp_path / "calls.log").touch()
    return {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "HOME": os.environ["HOME"],
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_COMMENT": str(tmp_path / "comment.md"),
        "GH_RETRY_BASE": "0",
        "GH_TOKEN": "x",
        "CONFIG": str(tmp_path / "factory.config.json"),
        "TARGET_REPO": "o/r",
        "OUT_DIR": str(out),
        "WORK_DIR": str(work),
        "INPUTS_DIR": str(inputs),
        "RUN_ID": "run-9",
        "ISSUE": "125",
        "PR": "7",
    }


def apply(tmp_path, env):
    p = subprocess.run([str(ROOT / "scripts/apply-proposal.sh"), "builder", "revise"], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    return p, (tmp_path / "calls.log").read_text().splitlines()


def test_revise_pushes_swaps_labels_readies_and_comments(tmp_path):
    env = make_env(tmp_path, 1)
    p, calls = apply(tmp_path, env)
    assert p.returncode == 0, p.stderr
    head = git("rev-parse", "HEAD", cwd=tmp_path / "work").strip()
    assert git("rev-parse", "feat/125-x", cwd=tmp_path / "origin.git").strip() == head
    assert any("pr edit 7" in c and "--add-label revise:attempted" in c and "--remove-label revise:requested" in c
               for c in calls)
    assert any(c.startswith("pr ready 7") for c in calls)
    body = (tmp_path / "comment.md").read_text()
    assert body.startswith("<!-- factory:revise -->\n\nFixed the thing")
    assert "@​someone" in body and "_Factory run run-9_" in body
    assert "revised PR #7 on feat/125-x" in p.stderr


def test_revise_without_commits_hands_to_a_human(tmp_path):
    env = make_env(tmp_path, 0)
    before = git("rev-parse", "feat/125-x", cwd=tmp_path / "origin.git")
    p, calls = apply(tmp_path, env)
    assert p.returncode != 0
    assert "revise produced no commits" in p.stderr
    assert any("pr edit 7" in c and "--add-label revise:attempted,needs-human" in c
               and "--remove-label revise:requested" in c for c in calls)
    assert not any(c.startswith(("pr ready", "pr comment")) for c in calls)
    assert git("rev-parse", "feat/125-x", cwd=tmp_path / "origin.git") == before


def test_revise_labels_are_swapped_before_the_push(tmp_path):
    env = make_env(tmp_path, 1)
    git("remote", "set-url", "--push", "origin", str(tmp_path / "missing.git"), cwd=tmp_path / "work")
    p, calls = apply(tmp_path, env)
    assert p.returncode != 0 and "revise push failed" in p.stderr
    edits = [c for c in calls if c.startswith("pr edit 7")]
    assert "--add-label revise:attempted" in edits[0] and "--remove-label revise:requested" in edits[0]
    assert "--add-label needs-human" in edits[1]
    assert not any(c.startswith(("pr ready", "pr comment")) for c in calls)


def test_revise_agent_prefix_is_checked(tmp_path):
    env = make_env(tmp_path, 1)
    env_fixer = {**env, "ISSUE": "125"}
    p = subprocess.run([str(ROOT / "scripts/apply-proposal.sh"), "bug-fixer", "revise"], cwd=ROOT, env=env_fixer,
                       capture_output=True, text=True)
    assert p.returncode != 0
    assert "branch prefix 'feat' not allowed for bug-fixer" in p.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert any("--add-label revise:attempted,needs-human" in c for c in calls)
    assert git("rev-parse", "feat/125-x", cwd=tmp_path / "origin.git") != git("rev-parse", "HEAD", cwd=tmp_path / "work")


def test_revise_branch_must_be_the_pr_head(tmp_path):
    env = make_env(tmp_path, 1)
    prop = tmp_path / "out" / "proposal.json"
    d = json.loads(prop.read_text())
    d["branch"] = "feat/125-other"
    prop.write_text(json.dumps(d))
    p, _ = apply(tmp_path, env)
    assert p.returncode != 0
    assert "is not the PR head" in p.stderr
