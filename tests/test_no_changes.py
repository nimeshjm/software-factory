import json

import pytest

from factory import run as run_mod
from tests.test_e2e import _inputs

EMPTY = {"branch": "fix/5-x", "title": "T", "body": "Closes #5", "files": [], "checks_passed": True, "notes": ""}


def _run(tmp_path, monkeypatch, script, agent, phase, proposal=EMPTY):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(script + [{"tool": "BuildProposal", "input": proposal}]))
    monkeypatch.setenv("FACTORY_FAKE_SCRIPT", str(sp))
    monkeypatch.setenv("FACTORY_RUN_ID", "r1")
    i = _inputs(tmp_path, {"protected": {"paths": ["package.json"]},
                           "toolchain": {"checks": [{"name": "test", "run": "true"}]}})
    (i / "run.json").write_text(json.dumps({"run_id": "r1", "agent": agent, "phase": phase,
                                            "dry_run": True, "issue_number": 5, "pr_number": 9}))
    out = tmp_path / "out"
    rc = run_mod.main(["--agent", agent, "--phase", phase, "--inputs", str(i), "--out", str(out),
                       "--dry-run", "--fake-model"])
    return rc, json.loads((out / "result.json").read_text()), out


@pytest.mark.parametrize("agent,phase", [("bug-fixer", "implement"), ("builder", "implement"), ("builder", "revise")])
def test_empty_proposal_is_no_changes(tmp_path, ws, monkeypatch, agent, phase):
    edit = {"tool": "edit_file", "input": {"path": "package.json", "old": "a", "new": "b"}}
    rc, res, out = _run(tmp_path, monkeypatch, [edit], agent, phase)
    assert rc == 1 and not res["success"] and res["stop_reason"] == "no_changes"
    assert res["error"].startswith("implement proposed no file changes and no findings; blocked: ")
    assert "package.json" in res["error"]
    assert json.loads((out / "proposal.json").read_text())["files"] == []


def test_no_changes_without_guard_event(tmp_path, ws, monkeypatch):
    rc, res, _ = _run(tmp_path, monkeypatch, [], "bug-fixer", "implement")
    assert rc == 1 and res["stop_reason"] == "no_changes"
    assert res["error"] == "implement proposed no file changes and no findings"


def test_blocked_details_capped_at_two(tmp_path, ws, monkeypatch):
    reads = [{"tool": "read_file", "input": {"path": f"../x{i}"}} for i in range(3)]
    _, res, _ = _run(tmp_path, monkeypatch, reads, "bug-fixer", "implement")
    assert res["error"].count("; blocked: ") == 2


def test_findings_only_is_success(tmp_path, ws, monkeypatch):
    rc, res, _ = _run(tmp_path, monkeypatch, [], "bug-fixer", "implement",
                      {**EMPTY, "findings_md": "cannot reproduce"})
    assert rc == 0 and res["success"] and res["stop_reason"] == "end_turn"


def test_files_present_is_success(tmp_path, ws, monkeypatch):
    rc, res, _ = _run(tmp_path, monkeypatch, [], "builder", "implement", {**EMPTY, "files": ["src/app.py"]})
    assert rc == 0 and res["stop_reason"] == "end_turn"
