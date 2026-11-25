"""gather-inputs.sh phases: an approved plan that lists no files is planned again, not implemented."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STUB = """#!/usr/bin/env bash
cat "$GH_COMMENTS"
"""


def _plan(files):
    return ("<!-- factory:plan -->\n<!-- factory:plan-files " + json.dumps({"files": files}) + " -->\n\n"
            "### Plan at a glance\n\nPLAN BODY\n")


@pytest.fixture
def run(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    inputs, out = tmp_path / "inputs", tmp_path / "out"
    inputs.mkdir()
    out.mkdir()

    def go(body, labels=("risk:high", "plan:approved", "status:plan-review")):
        (inputs / "issue.json").write_text(json.dumps({"number": 7, "labels": list(labels), "comments": []}))
        comments = tmp_path / "comments.json"
        comments.write_text(json.dumps([{"body": body, "user": {"login": "factory[bot]"}}] if body else []))
        gh_out = tmp_path / "gh_output"
        gh_out.write_text("")
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "CONFIG": str(ROOT / "factory.config.json"),
               "AGENT": "bug-fixer", "PHASE": "", "ISSUE": "7", "INPUTS_DIR": str(inputs), "OUT_DIR": str(out),
               "GITHUB_OUTPUT": str(gh_out), "GH_COMMENTS": str(comments), "GH_TOKEN": "x",
               "TARGET_REPO": "o/r"}
        proc = subprocess.run([str(ROOT / "scripts" / "gather-inputs.sh"), "phases"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return proc, gh_out.read_text(), json.loads((inputs / "issue.json").read_text())["labels"]

    return go


def test_approved_plan_with_files_runs_implement_only(run, tmp_path):
    _, output, labels = run(_plan(["package.json"]))
    assert "phases=implement\n" in output and "plan:approved" in labels
    assert "PLAN BODY" in (tmp_path / "out" / "plan.md").read_text()


def test_approved_empty_plan_plans_again_without_the_approval(run, tmp_path):
    proc, output, labels = run(_plan([]))
    assert "phases=research plan implement\n" in output
    assert "plan:approved" not in labels and "risk:high" in labels
    assert "lists no files: planning again" in proc.stderr
    assert not (tmp_path / "out" / "plan.md").exists()


def test_empty_plan_on_a_spike_and_plans_without_the_marker_still_implement(run):
    _, output, _ = run(_plan([]), labels=("spike", "plan:approved"))
    assert "phases=implement\n" in output
    _, output, _ = run("<!-- factory:plan -->\nold plan without a files marker\n")
    assert "phases=implement\n" in output
