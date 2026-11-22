import json
import os
import subprocess
from pathlib import Path

import pytest

from factory.policy.reuse_review import reuse_review
from factory.schemas import proposal_model_for

ROOT = Path(__file__).resolve().parent.parent
SHA = "9b72e4e5fb398d73bb4e4d6cf6252e5233605d95"


def _pr(author="app/dependabot", sha=SHA, body="<!-- factory:review -->\n**Verdict:** approve\n\n### Agent checks\n"):
    return {"number": 132, "author": author,
            "factory_review": {"body": body, "commit_id": sha, "submitted_at": "2026-11-18T23:31:42Z"}}


def test_reuses_verdict_for_unchanged_dependabot_head():
    p = reuse_review(_pr(), SHA)
    assert p["verdict"] == "approve"
    assert SHA[:12] in p["summary_md"]
    proposal_model_for("reviewer", "review").model_validate(p)  # apply's validate_and_guard must accept it


@pytest.mark.parametrize("verdict", ["request_changes", "comment", "needs_human"])
def test_every_verdict_is_carried(verdict):
    assert reuse_review(_pr(body=f"<!-- factory:review -->\n**Verdict:** {verdict}\n"), SHA)["verdict"] == verdict


@pytest.mark.parametrize("pr,head", [
    (_pr(), "0" * 40),                                                   # new push: review the new head
    (_pr(author="app/software-factory-agent-bot"), SHA),                 # factory PR: verdict drives revise
    (_pr(author="nimeshjm"), SHA),                                       # human PR
    (_pr(body="<!-- factory:review -->\n## Review\napprove"), SHA),      # pre-2026-11-13 body, no verdict line
    (_pr(body="**Verdict:** approve"), SHA),                             # not a factory review
    ({"author": "app/dependabot", "factory_review": None}, SHA),          # never reviewed
    (_pr(), ""),                                                         # head unknown
])
def test_runs_the_model_otherwise(pr, head):
    assert reuse_review(pr, head) is None


def _run_phases(tmp_path, pr, head, agent="reviewer", phases="review"):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "pr.json").write_text(json.dumps(pr))
    env = {**os.environ, "AGENT": agent, "PHASES": phases, "HEAD_SHA": head, "FACTORY_RUN_ID": "r1",
           "UV_PROJECT": str(ROOT), "PYTHONPATH": str(ROOT)}
    return subprocess.run([str(ROOT / "scripts" / "run-phases.sh")], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)


def test_run_phases_skips_the_model(tmp_path):
    proc = _run_phases(tmp_path, _pr(), SHA)
    assert proc.returncode == 0, proc.stderr
    assert "no model call" in proc.stdout
    out = tmp_path / "out"
    assert (out / "phases_ok.txt").read_text().strip() == "review"
    assert json.loads((out / "phases" / "review" / "proposal.json").read_text())["verdict"] == "approve"
    res = json.loads((out / "phases" / "review" / "result.json").read_text())
    assert res["success"] is True and res["stop_reason"] == "reused_review" and res["model_calls"] == 0
    assert res["usage"]["total_tokens"] == 0
