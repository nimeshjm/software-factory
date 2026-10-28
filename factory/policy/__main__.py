"""CLI for the pure policy functions.

  python -m factory.policy classify-autonomy --pr inputs/pr.json --factory inputs/factory.json
  python -m factory.policy merge-policy --pr inputs/pr.json --factory inputs/factory.json \
      [--plan-files out/plan_files.json] --trigger cron|webhook
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .autonomy import classify_autonomy
from .merge import _bumped_deps, merge_policy


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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
    ns = ap.parse_args(argv)

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
