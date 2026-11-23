"""scripts/apply-proposal.sh builder plan: Gate cell and labels for a protected-path hold (plan-gate CLI stubbed)."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG"
while [ $# -gt 0 ]; do
  if [ "$1" = "-F" ]; then cp "${2#body=@}" "$STUB_COMMENT"; fi
  shift
done
exit 0
"""
# Only the plan-gate call prints; schema validation and the output guard just succeed.
UV = """#!/usr/bin/env bash
case "$*" in *factory.policy*) printf '%s' "$STUB_GATE" ;; *) ;; esac
exit 0
"""


def apply(tmp_path, gate, files=("package.json",)):
    inputs, out, bindir = tmp_path / "inputs", tmp_path / "out", tmp_path / "bin"
    for d in (inputs, out, bindir):
        d.mkdir()
    (inputs / "factory.json").write_text("{}")
    (out / "proposal.json").write_text(json.dumps({
        "steps": ["a"], "files": list(files), "needs_human": False, "risk_notes": [], "plan_md": "Plan."}))
    for name, body in (("gh", GH), ("uv", UV)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    (tmp_path / "calls.log").touch()
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "HOME": os.environ["HOME"],
        "STUB_LOG": str(tmp_path / "calls.log"), "STUB_COMMENT": str(tmp_path / "comment.md"),
        "STUB_GATE": json.dumps(gate), "GH_RETRY_BASE": "0", "GH_TOKEN": "x", "TARGET_REPO": "o/r",
        "OUT_DIR": str(out), "INPUTS_DIR": str(inputs), "WORK_DIR": str(tmp_path / "work"),
        "RUN_ID": "run-9", "ISSUE": "121",
    }
    p = subprocess.run([str(ROOT / "scripts/apply-proposal.sh"), "builder", "plan"], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return (tmp_path / "comment.md").read_text(), (tmp_path / "calls.log").read_text().splitlines()


def test_protected_hold_explains_and_labels_needs_human(tmp_path):
    body, calls = apply(tmp_path, {"hold": True, "reasons": ["protected:package.json", "protected:.github/ci.yml"]},
                        files=("package.json", ".github/ci.yml"))
    assert ("| Gate | held: plan edits protected paths `package.json`, `.github/ci.yml`; agents cannot edit them "
            "(factory.yml protected.paths). Make the change by hand or change the profile, then remove "
            "needs-human and set status:backlog |") in body
    assert any("issue edit 121" in c and "--add-label status:plan-review,needs-human" in c for c in calls)


def test_protected_text_wins_over_needs_human(tmp_path):
    body, calls = apply(tmp_path, {"hold": True, "reasons": ["needs_human", "protected:package.json"]})
    assert "held: plan edits protected paths `package.json`;" in body
    assert "held: needs_human" not in body
    assert any("--add-label status:plan-review,needs-human" in c for c in calls)


def test_label_hold_is_unchanged(tmp_path):
    body, calls = apply(tmp_path, {"hold": True, "reasons": ["label:risk:high"]})
    assert "held: label:risk:high; add plan:approved to continue" in body
    assert any("--add-label status:plan-review " in c + " " and "needs-human" not in c for c in calls)
