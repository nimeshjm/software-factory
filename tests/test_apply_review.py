"""scripts/apply-proposal.sh post_review: the verdict line, and COMMENT on the App's own PRs."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A stub gh that records the --input body of every `api -X POST .../reviews` as $STUB_DIR/post-<n>.json.
# The first STUB_FAILS posts fail with a 422.
STUB = """#!/usr/bin/env bash
if [ "$1" = api ] && [ "$2" = -X ] && [ "$3" = POST ]; then
  n=$(ls "$STUB_DIR" | grep -c '^post-')
  while [ $# -gt 0 ]; do
    if [ "$1" = "--input" ]; then cp "$2" "$STUB_DIR/post-$((n + 1)).json"; fi
    shift
  done
  if [ "$n" -lt "${STUB_FAILS:-0}" ]; then echo "HTTP 422" >&2; exit 1; fi
fi
exit 0
"""


def run_post_review(tmp_path, author, verdict, fails=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    stubdir = tmp_path / "stub"
    stubdir.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "pr.json").write_text(json.dumps({"number": 7, "author": author}))
    (inputs / "factory.json").write_text(json.dumps({}))
    prop = tmp_path / "proposal.json"
    prop.write_text(json.dumps({"verdict": verdict, "summary_md": "Looks off.", "inline_comments": []}))
    # Source the real function out of the script (the script itself runs the dispatch on source).
    snippet = f"""
set -euo pipefail
source scripts/lib.sh
TMP="{tmp_path}/tmp"; mkdir -p "$TMP"; PR=7
eval "$(sed -n '/^post_review() {{/,/^}}/p' scripts/apply-proposal.sh)"
amigos_footer() {{ :; }}
post_review "{prop}" abc123
"""
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "HOME": os.environ["HOME"],
           "STUB_DIR": str(stubdir), "STUB_FAILS": str(fails), "TARGET_REPO": "o/r",
           "INPUTS_DIR": str(inputs), "CONFIG": str(ROOT / "factory.config.json")}
    p = subprocess.run(["bash", "-c", snippet], cwd=ROOT, env=env, capture_output=True, text=True)
    posts = [json.loads(f.read_text()) for f in sorted(stubdir.glob("post-*.json"))]
    return p, posts


def test_factory_pr_request_changes_posts_comment(tmp_path):
    p, posts = run_post_review(tmp_path, "app/software-factory-agent-bot", "request_changes")
    assert p.returncode == 0, p.stderr
    assert len(posts) == 1
    assert posts[0]["event"] == "COMMENT"
    assert posts[0]["body"].startswith("<!-- factory:review -->\n**Verdict:** request_changes\n")
    assert "Looks off." in posts[0]["body"]


def test_human_pr_request_changes_posts_request_changes(tmp_path):
    p, posts = run_post_review(tmp_path, "alice", "request_changes")
    assert p.returncode == 0, p.stderr
    assert posts[0]["event"] == "REQUEST_CHANGES"
    assert posts[0]["body"].startswith("<!-- factory:review -->\n**Verdict:** request_changes\n")


def test_fallback_still_runs(tmp_path):
    p, posts = run_post_review(tmp_path, "alice", "request_changes", fails=2)
    assert p.returncode == 0, p.stderr
    assert [x["event"] for x in posts] == ["REQUEST_CHANGES", "REQUEST_CHANGES", "COMMENT"]


# apply_review's not-mergeable path, with the policy CLIs and fetch_pr_json stubbed.
REVIEW_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG"
if [ "$1 $2" = "pr view" ]; then echo abc123; fi
if [ "$1" = api ]; then echo '[]'; fi
exit 0
"""


def run_apply_review(tmp_path, labels, author="app/software-factory-agent-bot", head="feat/125-x",
                     verdict="request_changes", ci_fix=False):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(REVIEW_STUB)
    (bindir / "gh").chmod(0o755)
    (tmp_path / "calls.log").touch()
    prop = tmp_path / "proposal.json"
    prop.write_text(json.dumps({"verdict": verdict, "ci_fix": {"attempted": ci_fix}}))
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps({"author": author, "headRefName": head, "labels": labels, "body": ""}))
    # No `-u`: apply_review expands an empty array, which bash 3.2 (macOS) reports as unbound.
    snippet = f"""
set -eo pipefail
source scripts/lib.sh
TMP="{tmp_path}/tmp"; mkdir -p "$TMP"; PR=7; OUT_DIR="{tmp_path}"
eval "$(sed -n '/^apply_review() {{/,/^}}/p' scripts/apply-proposal.sh)"
proposal_path() {{ printf '%s' "{prop}"; }}
validate_and_guard() {{ :; }}
post_review() {{ :; }}
fetch_pr_json() {{ cat "{fresh}"; }}
uvpy() {{ case "$3" in classify-autonomy) echo lit ;; *) echo '{{"merge": false, "reasons": []}}' ;; esac; }}
apply_review
"""
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "HOME": os.environ["HOME"],
           "STUB_LOG": str(tmp_path / "calls.log"), "TARGET_REPO": "o/r", "GH_RETRY_BASE": "0",
           "INPUTS_DIR": str(tmp_path), "CONFIG": str(ROOT / "factory.config.json")}
    p = subprocess.run(["bash", "-c", snippet], cwd=ROOT, env=env, capture_output=True, text=True)
    return p, (tmp_path / "calls.log").read_text().splitlines()


def test_review_requests_revise_on_a_factory_pr(tmp_path):
    p, calls = run_apply_review(tmp_path, [])
    assert p.returncode == 0, p.stderr
    assert any("--add-label revise:requested" in c for c in calls)
    assert "revise requested" in p.stderr


def test_review_hands_over_after_a_revise(tmp_path):
    p, calls = run_apply_review(tmp_path, ["revise:attempted"])
    assert p.returncode == 0, p.stderr
    assert any("--add-label needs-human" in c for c in calls)
    assert not any("revise:requested" in c for c in calls)


def test_review_never_revises_a_needs_human_pr(tmp_path):
    p, calls = run_apply_review(tmp_path, ["needs-human"])
    assert p.returncode == 0, p.stderr
    assert "needs-human present; no revise" in p.stderr
    assert not any("revise:requested" in c for c in calls)


def test_review_leaves_human_prs_alone(tmp_path):
    p, calls = run_apply_review(tmp_path, [], author="alice")
    assert p.returncode == 0, p.stderr
    assert not any("revise:requested" in c for c in calls)
