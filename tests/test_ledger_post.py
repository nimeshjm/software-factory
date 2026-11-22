import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ledger-post.sh"


def _run(tmp_path, results, proposal=None, **env):
    out = tmp_path / "out"
    for phase, res in results.items():
        d = out / "phases" / phase
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps(res))
    out.mkdir(exist_ok=True)
    if proposal is not None:
        (out / "phases" / "review").mkdir(parents=True, exist_ok=True)
        (out / "phases" / "review" / "proposal.json").write_text(json.dumps(proposal))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    body = tmp_path / "body.json"
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do\n'
        f'  if [ "$1" = "--data" ]; then printf %s "$2" > {body}; fi\n  shift\ndone\n')
    curl.chmod(0o755)
    e = {k: v for k, v in os.environ.items() if k not in ("PHASES", "PHASE", "DRY_RUN", "DENIED", "JOB_STATUS", "AGENT")}
    e.update(PATH=f"{bin_dir}:{e['PATH']}", FACTORY_URL="https://f.example", FACTORY_TOKEN="t",
             RUN_ID="r1", OUT_DIR=str(out), **env)
    subprocess.run([str(SCRIPT), "finished"], env=e, check=True, capture_output=True, text=True)
    return json.loads(body.read_text())


def _res(success=True, throttles=0, error=None):
    r = {"success": success, "stop_reason": "end_turn" if success else "error", "model_calls": 1,
         "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "throttles": throttles}
    if error is not None:
        r["error"] = error
    return r


def test_all_phases_succeed(tmp_path):
    b = _run(tmp_path, {"research": _res(throttles=1), "plan": _res(throttles=2), "implement": _res()},
             PHASES="research plan implement", DRY_RUN="true")
    assert b["phase"] == "implement"
    assert b["error"] is None
    assert b["throttles"] == 3
    assert b["dry_run"] is True
    assert b["status"] == "success"


def test_failed_phase_uses_phases_order_and_clips_error(tmp_path):
    b = _run(tmp_path, {"research": _res(), "plan": _res(False, 2, "x" * 500), "implement": _res(False, 0, "later")},
             PHASES="research plan implement")
    assert b["phase"] == "plan"
    assert b["error"] == "x" * 300
    assert b["throttles"] == 2
    assert b["dry_run"] is False
    assert b["status"] == "failed"


def test_glob_order_when_phases_unset(tmp_path):
    b = _run(tmp_path, {"implement": _res(), "plan": _res()})
    assert b["phase"] == "plan"  # alphabetical last


def test_denied_without_results(tmp_path):
    b = _run(tmp_path, {}, DENIED="true", PHASE="review", DRY_RUN="true")
    assert (b["status"], b["phase"], b["error"], b["throttles"], b["dry_run"]) == ("denied", "review", None, 0, True)


def test_timeout_without_phase(tmp_path):
    b = _run(tmp_path, {})
    assert (b["status"], b["phase"], b["throttles"], b["dry_run"]) == ("timeout", None, 0, False)


def test_reviewer_verdict_from_proposal(tmp_path):
    b = _run(tmp_path, {"review": _res()}, {"verdict": "request_changes"}, AGENT="reviewer", PHASES="review")
    assert b["verdict"] == "request_changes"


def test_reviewer_without_proposal_has_null_verdict(tmp_path):
    assert _run(tmp_path, {"review": _res()}, AGENT="reviewer", PHASES="review")["verdict"] is None


def test_non_reviewer_verdict_is_null(tmp_path):
    b = _run(tmp_path, {"review": _res()}, {"verdict": "approve"}, AGENT="builder", PHASES="review")
    assert b["verdict"] is None


def test_denied_verdict_is_null(tmp_path):
    assert _run(tmp_path, {}, {"verdict": "approve"}, AGENT="reviewer", DENIED="true")["verdict"] is None
