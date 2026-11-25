import json

from factory.prompts import PLAN_MARKER, build_task_message

FACTORY = {"dark_paths": ["docs/**"], "limits": {"max_diff_lines": 300}, "protected": {"paths": ["migrations/**"]},
           "risk": {"high_paths": ["x"]}, "secrets": {"names": ["S"]}, "honeycomb": {"enabled": True}}


def _inputs(tmp_path):
    d = tmp_path / "inputs"
    d.mkdir()
    (d / "run.json").write_text(json.dumps({"issue_number": 7}))
    (d / "issue.json").write_text(json.dumps({"number": 7, "title": "t", "body": "b", "labels": [], "comments": [
        {"author": {"login": "bot"}, "body": PLAN_MARKER + "\n### Plan at a glance\nPLANTEXT"},
        {"author": {"login": "me"}, "body": "a human note"}]}))
    (d / "issues.json").write_text(json.dumps([{"number": 120, "title": "OTHER-ISSUE"}]))
    (d / "prs.json").write_text(json.dumps([{"number": 5, "title": "OTHER-PR"}]))
    (d / "factory.json").write_text(json.dumps(FACTORY))
    return d


def test_implement_drops_work_lists_and_profile_keys(tmp_path):
    d = _inputs(tmp_path)
    msg = build_task_message(d, "builder", "implement")
    assert "OTHER-ISSUE" not in msg and "OTHER-PR" not in msg and "## open issues" not in msg
    assert '"dark_paths":["docs/**"]' in msg and '"max_diff_lines":300' in msg
    assert "migrations/**" not in msg and "high_paths" not in msg and '"honeycomb"' not in msg


def test_research_and_triage_keep_the_lists_and_full_factory_json(tmp_path):
    d = _inputs(tmp_path)
    for agent, phase in (("builder", "research"), ("triage", "triage"), ("incident-responder", "respond")):
        msg = build_task_message(d, agent, phase)
        assert "OTHER-ISSUE" in msg and "OTHER-PR" in msg
        assert "migrations/**" in msg and '"honeycomb"' in msg


def test_plan_comment_skipped_when_plan_md_is_attached(tmp_path):
    d = _inputs(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    assert "PLANTEXT" in build_task_message(d, "builder", "implement", extra_dirs=[out])
    (out / "plan.md").write_text("### Plan at a glance\nPLANTEXT")
    msg = build_task_message(d, "builder", "implement", extra_dirs=[out])
    assert msg.count("PLANTEXT") == 1 and '<artifact name="plan.md">' in msg
    assert "a human note" in msg
