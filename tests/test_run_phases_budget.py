"""scripts/run-phases.sh hands each phase what the earlier phases left of the run's paid allowance."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Stub `uv`: records PAID_ALLOWANCE_USD per call and writes a successful result.json that spent $0.05.
STUB_UV = """#!/usr/bin/env bash
printf '%s\\n' "$PAID_ALLOWANCE_USD" >>"$ALLOWANCE_LOG"
printf '{"success": true, "stop_reason": "end_turn", "paid_cost_usd": 0.05}' >out/result.json
printf '{}' >out/proposal.json
"""


def _run(tmp_path, allowance):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "uv").write_text(STUB_UV)
    (bindir / "uv").chmod(0o755)
    (tmp_path / "out").mkdir()
    if allowance is not None:
        (tmp_path / "out" / "paid_allowance.txt").write_text(f"{allowance}\n")
    log = tmp_path / "allowance.log"
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AGENT": "triage",
           "PHASES": "triage review", "ALLOWANCE_LOG": str(log)}
    p = subprocess.run([str(ROOT / "scripts/run-phases.sh")], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return [float(x) for x in log.read_text().split()]


def test_phases_share_the_run_allowance(tmp_path):
    first, second = _run(tmp_path, 0.12)
    assert first == 0.12 and round(second, 6) == 0.07


def test_spent_allowance_floors_at_zero(tmp_path):
    assert _run(tmp_path, 0.03) == [0.03, 0]


def test_no_allowance_file_is_zero(tmp_path):
    assert _run(tmp_path, None) == [0, 0]
