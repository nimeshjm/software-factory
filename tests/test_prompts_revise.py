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
