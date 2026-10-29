"""CLI for the pure policy functions.

  python -m factory.policy classify-autonomy --pr inputs/pr.json --factory inputs/factory.json
  python -m factory.policy merge-policy --pr inputs/pr.json --factory inputs/factory.json \
      [--plan-files out/plan_files.json] --trigger cron|webhook
  python -m factory.policy branch --agent builder|bug-fixer --issue inputs/issue.json
  python -m factory.policy check-findings --proposal F --results R [R ...]
  python -m factory.policy evidence-table --results R [R ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .author import slug
from .autonomy import classify_autonomy
from .findings import check_findings, evidence_table
from .merge import _bumped_deps, merge_policy


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_result(path: str) -> dict:
    """A missing result.json counts as no tool calls."""
    try:
        return _load(path)
    except FileNotFoundError:
        return {"tool_calls": []}


def _is_dependabot(pr: dict) -> bool:
    author = str(pr.get("author") or "")
    return author in {"app/dependabot", "dependabot[bot]", "dependabot"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m factory.policy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("classify-autonomy")
    a.add_argument("--pr", required=True)
    a.add_argument("--factory", required=True)
    m = sub.add_parser("merge-policy")
    m.add_argument("--pr", required=True)
    m.add_argument("--factory", required=True)
    m.add_argument("--plan-files")
    m.add_argument("--trigger", default="webhook")
    b = sub.add_parser("branch", help="deterministic feat|fix/<N>-<slug> branch for an issue")
    b.add_argument("--agent", required=True, choices=["builder", "bug-fixer"])
    b.add_argument("--issue", required=True)
    cf = sub.add_parser("check-findings", help="verify HTTP statuses claimed in spike findings against tool_calls")
    cf.add_argument("--proposal", required=True)
    cf.add_argument("--results", nargs="+", required=True)
    et = sub.add_parser("evidence-table", help="markdown table of fetched URLs and searches from result.json files")
    et.add_argument("--results", nargs="+", required=True)
    ns = ap.parse_args(argv)

    if ns.cmd in ("check-findings", "evidence-table"):
        results = [_load_result(p) for p in ns.results]
        if ns.cmd == "evidence-table":
            sys.stdout.write(evidence_table(results))
            return 0
        violations = check_findings(str(_load(ns.proposal).get("findings_md") or ""), results)
        json.dump({"ok": not violations, "violations": violations}, sys.stdout)
        sys.stdout.write("\n")
        return 1 if violations else 0

    if ns.cmd == "branch":
        issue = _load(ns.issue)
        prefix = "feat" if ns.agent == "builder" else "fix"
        sys.stdout.write(f"{prefix}/{issue['number']}-{slug(str(issue.get('title') or ''))}\n")
        return 0

    pr = _load(ns.pr)
    factory = _load(ns.factory)
    dependabot = _is_dependabot(pr)
    if ns.cmd == "classify-autonomy":
        deps = _bumped_deps(pr) if dependabot else []
        sys.stdout.write(classify_autonomy(pr.get("files") or [], factory, deps) + "\n")
        return 0
    plan_files = _load(ns.plan_files) if ns.plan_files else None
    decision = merge_policy(pr, factory, plan_files, dependabot, ns.trigger)
    json.dump(asdict(decision), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
