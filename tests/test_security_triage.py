import copy
import json

import pytest
from pydantic import ValidationError

from factory import run as run_mod
from factory.prompts import build_task_message
from factory.schemas import SecurityFinding, SecurityProposal, proposal_model_for

SECURITY = {
    "feeds": {
        "code_scanning": {"available": True, "status": 200, "message": None},
        "dependabot": {"available": True, "status": 200, "message": None},
        "secret_scanning": {"available": False, "status": 404, "message": "Secret scanning is disabled on this repository."},
    },
    "code_scanning": [{
        "number": 1, "signature": "code_scanning:js/stack-trace-exposure:src/loginProbe.ts",
        "rule_id": "js/stack-trace-exposure", "severity": "warning", "security_severity": "medium",
        "description": "...", "path": "src/loginProbe.ts", "start_line": 73, "message": "...",
        "html_url": "https://github.com/o/r/security/code-scanning/1", "created_at": "2026-10-01T00:00:00Z"}],
    "dependabot": [],
    "secret_scanning": {"count": 0, "types": []},
    "dependabot_prs": [],
    "tracked_issues": [],
}

FINDING = {"source": "code_scanning", "signature": "code_scanning:js/x:src/a.ts", "alert_numbers": [1],
           "title": "js/x: leak", "body": "b", "priority": 2, "labels": ["risk:low"], "existing_issue": None}


def _inputs(tmp_path, security=SECURITY):
    i = tmp_path / "inputs"
    i.mkdir()
    (i / "factory.json").write_text(json.dumps({"toolchain": {"checks": [{"name": "test", "run": "true"}]}}))
    (i / "run.json").write_text(json.dumps({"run_id": "r1", "agent": "security-triage", "phase": "triage",
                                            "dry_run": True}))
    if security is not None:
        (i / "security.json").write_text(json.dumps(security))
    return i


def _run(tmp_path, i):
    out = tmp_path / "out"
    rc = run_mod.main(["--agent", "security-triage", "--phase", "triage", "--inputs", str(i), "--out", str(out),
                       "--dry-run", "--fake-model"])
    return rc, out, json.loads((out / "result.json").read_text())


def test_schema_round_trip_and_rejection():
    p = SecurityProposal.model_validate({"findings": [FINDING], "resolved": [
        {"issue": 7, "signature": "dependabot:npm:lodash", "note": "fixed"}], "summary": "s"})
    assert SecurityProposal.model_validate_json(p.model_dump_json()) == p
    assert SecurityProposal(summary="s").findings == []
    with pytest.raises(ValidationError):
        SecurityFinding.model_validate({**FINDING, "source": "sonar"})
    with pytest.raises(ValidationError):
        SecurityFinding.model_validate({**FINDING, "priority": 4})


def test_proposal_model_for():
    assert proposal_model_for("security-triage", "triage") is SecurityProposal


def test_task_message_wraps_security_json(tmp_path):
    msg = build_task_message(_inputs(tmp_path), "security-triage", "triage")
    assert '<untrusted source="security.json">' in msg
    assert "code_scanning:js/stack-trace-exposure:src/loginProbe.ts" in msg
    start = msg.index('<untrusted source="security.json">')
    assert "src/loginProbe.ts" in msg[start:msg.index("</untrusted>", start)]


def test_fake_model_end_to_end(tmp_path, ws, monkeypatch):
    monkeypatch.setenv("FACTORY_RUN_ID", "r1")
    rc, out, res = _run(tmp_path, _inputs(tmp_path))
    assert rc == 0, res
    assert res["success"] and res["stop_reason"] == "end_turn"
    assert json.loads((out / "proposal.json").read_text()) == {"findings": [], "resolved": [], "summary": "fake"}


def _no_model(monkeypatch):
    built = []
    monkeypatch.setattr(run_mod, "_build_model", lambda *a, **k: built.append(1))
    return built


def test_no_inputs_when_file_missing(tmp_path, ws, monkeypatch):
    built = _no_model(monkeypatch)
    rc, out, res = _run(tmp_path, _inputs(tmp_path, security=None))
    assert rc == 1 and not res["success"] and res["stop_reason"] == "no_inputs"
    assert res["model_calls"] == 0 and not built and not (out / "proposal.json").exists()
    assert "security.json" in res["error"]


def test_no_inputs_when_unparseable(tmp_path, ws, monkeypatch):
    built = _no_model(monkeypatch)
    i = _inputs(tmp_path)
    (i / "security.json").write_text("{not json")
    rc, _, res = _run(tmp_path, i)
    assert rc == 1 and res["stop_reason"] == "no_inputs" and res["model_calls"] == 0 and not built


def test_no_inputs_when_all_feeds_unavailable(tmp_path, ws, monkeypatch):
    built = _no_model(monkeypatch)
    sec = copy.deepcopy(SECURITY)
    sec["feeds"] = {
        "code_scanning": {"available": False, "status": 403, "message": "Resource not accessible by integration"},
        "dependabot": {"available": False, "status": 403, "message": "Resource not accessible by integration"},
        "secret_scanning": {"available": False, "status": 404, "message": "Secret scanning is disabled."},
    }
    rc, out, res = _run(tmp_path, _inputs(tmp_path, sec))
    assert rc == 1 and not res["success"] and res["stop_reason"] == "no_inputs"
    assert res["model_calls"] == 0 and not built and not (out / "proposal.json").exists()
    for feed in ("code_scanning: 403 Resource not accessible by integration",
                 "dependabot: 403 Resource not accessible by integration",
                 "secret_scanning: 404 Secret scanning is disabled."):
        assert feed in res["error"]


def test_body_literal_backslash_n_is_unescaped():
    from factory.schemas.models import SecurityFinding

    base = dict(source="code_scanning", signature="code_scanning:r:p", alert_numbers=[1], title="t", priority=2)
    f = SecurityFinding(body="line one\\nline two", **base)
    assert f.body == "line one\nline two"
    # a body that already has real newlines is left alone, even if it mentions the escape
    f2 = SecurityFinding(body="real\nnewline and literal \\n kept", **base)
    assert f2.body == "real\nnewline and literal \\n kept"
