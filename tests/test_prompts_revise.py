import json
from pathlib import Path

import pytest

from factory.prompts import build_task_message, load_system_prompt

ROOT = Path(__file__).resolve().parent.parent
OLD_LINE = "2. `inputs/run.json` and the inputs named in this prompt."
PROMPTS = sorted((ROOT / "agents").rglob("*.md"))


@pytest.mark.parametrize("agent", ["builder", "bug-fixer"])
def test_revise_prompt_loads(agent):
    text = load_system_prompt(agent, "revise")
    assert "proposal" in text.lower()
    assert "BuildProposal" in text and "run_checks" in text


def test_prompt_lint():
    raw = [p for p in PROMPTS if p.name != "README.md" and "_shared" not in p.parts]
    assert raw
    for p in PROMPTS:
        assert OLD_LINE not in p.read_text(encoding="utf-8"), p
    for p in raw:
        t = p.read_text(encoding="utf-8")
        assert t.count("{{target_profile}}") == 1, p
        assert "proposal" in t.lower(), p


def _inputs(tmp_path, **pr_extra):
    (tmp_path / "run.json").write_text(json.dumps({"run_id": "r1", "pr_number": 7}))
    pr = {"number": 7, "title": "t", "body": "b", "headRefName": "feat/3-x", "diff": "", **pr_extra}
    (tmp_path / "pr.json").write_text(json.dumps(pr))
    return tmp_path


def test_factory_review_inlined(tmp_path):
    fr = {"body": "agent check failed: tests", "commit_id": "abcdef1234", "submitted_at": "2026-01-01T00:00:00Z"}
    msg = build_task_message(_inputs(tmp_path, factory_review=fr), "builder", "revise")
    assert '<untrusted source="previous factory review for pr #7 (commit abcdef1, 2026-01-01T00:00:00Z)"' in msg
    assert "agent check failed: tests" in msg


@pytest.mark.parametrize("extra", [{"factory_review": None}, {}])
def test_factory_review_omitted(tmp_path, extra):
    msg = build_task_message(_inputs(tmp_path, **extra), "builder", "revise")
    assert "previous factory review" not in msg


PR137_REVIEW = """<!-- factory:review -->
**Verdict:** request_changes

### Agent checks
| Check | Status | Detail |
|---|---|---|
| conventions | fail | src/workflows/ingest.ts uses getAllTeams instead of getTeams; missing test file |
| secrets | pass | no secrets detected |
| plan_drift | fail | missing planned files migrations/0006_player_awards.sql |
| ci | fail | build-and-test check failed on typecheck |

### Verdict
request_changes: Typecheck failed due to non-existent db export (`getAllTeams` vs `getTeams`).
"""


def test_revise_checklist_comes_first(tmp_path):
    checks = [{"name": "build-and-test", "status": "completed", "conclusion": "failure"},
              {"name": "lint", "status": "completed", "conclusion": "success"}]
    msg = build_task_message(_inputs(tmp_path, factory_review={"body": PR137_REVIEW}, checks=checks), "builder", "revise")
    todo = msg.index("## What to fix")
    assert todo < msg.index("## Pull request #7")
    block = msg[todo:msg.index("</untrusted>", todo)]
    assert "- conventions: src/workflows/ingest.ts uses getAllTeams instead of getTeams; missing test file" in block
    assert "- plan_drift: missing planned files migrations/0006_player_awards.sql" in block
    assert "- review verdict: Typecheck failed" in block
    assert "- CI check failed: build-and-test" in block
    assert "secrets" not in block and "lint" not in block


def test_no_checklist_outside_revise_or_without_failures(tmp_path):
    msg = build_task_message(_inputs(tmp_path, factory_review={"body": "all good"}), "builder", "revise")
    assert "## What to fix" not in msg
    msg2 = build_task_message(_inputs(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path,
                                      factory_review={"body": PR137_REVIEW}), "reviewer", "review")
    assert "## What to fix" not in msg2


def test_plan_comment_is_inlined_once(tmp_path):
    plan = "<!-- factory:plan -->\nthe plan"
    _inputs(tmp_path, plan_comment=plan)
    (tmp_path / "issue.json").write_text(json.dumps({"number": 3, "title": "t", "body": "b", "labels": [],
        "comments": [{"author": "bot", "body": plan}, {"author": "owner", "body": "keep me"}]}))
    msg = build_task_message(tmp_path, "builder", "revise")
    assert msg.count("the plan") == 1 and "keep me" in msg


def test_phase_context_globs(tmp_path):
    _inputs(tmp_path)
    ctx = tmp_path / "context"
    ctx.mkdir()
    for name in ("AGENTS.md", "conventions.md", "product-spec.md"):
        (ctx / name).write_text(name)
    every = build_task_message(tmp_path, "builder", "revise")
    # context files are stored by basename, so a phase selects them by name (".agents/*.md" would match AGENTS.md too)
    some = build_task_message(tmp_path, "builder", "revise", context_globs=["conventions.md", "architecture.md"])
    assert all(f'file="{n}"' in every for n in ("AGENTS.md", "conventions.md", "product-spec.md"))
    assert 'file="conventions.md"' in some and 'file="AGENTS.md"' not in some and 'file="product-spec.md"' not in some
