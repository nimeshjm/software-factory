"""scripts/gather-inputs.sh phases: default sequence is .phases only; an explicit PHASE may also be an extra phase."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A stub gh that fails loudly: mode_phases must not need the network for these cases.
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG"
exit 0
"""


@pytest.fixture
def run(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    inputs, out = tmp_path / "inputs", tmp_path / "out"
    inputs.mkdir()
    (inputs / "issue.json").write_text(json.dumps({"number": 7, "labels": ["agent:build"], "comments": []}))
    gh_out = tmp_path / "gh_output"
    gh_out.write_text("")

    def go(phase, issue="7"):
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "CONFIG": str(ROOT / "factory.config.json"),
               "AGENT": "builder", "PHASE": phase, "ISSUE": issue, "INPUTS_DIR": str(inputs), "OUT_DIR": str(out),
               "GITHUB_OUTPUT": str(gh_out), "STUB_LOG": str(tmp_path / "gh.log"), "GH_TOKEN": "x"}
        proc = subprocess.run([str(ROOT / "scripts" / "gather-inputs.sh"), "phases"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        return proc, gh_out.read_text(), out

    return go


def test_explicit_revise_emits_revise_and_no_plan(run):
    proc, output, out = run("revise")
    assert proc.returncode == 0, proc.stderr
    assert "phases=revise\n" in output
    assert not (out / "plan.md").exists()


def test_default_sequence_excludes_extra_phases(run):
    proc, output, _ = run("")
    assert proc.returncode == 0, proc.stderr
    assert "phases=research plan implement\n" in output


def test_unknown_phase_is_rejected(run):
    proc, output, _ = run("bogus")
    assert proc.returncode != 0
    assert "revise" in proc.stderr + proc.stdout
    assert "phases=" not in output
