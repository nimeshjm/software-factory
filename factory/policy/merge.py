"""Merge policy. Pure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .autonomy import classify_autonomy

GREEN = {"success", "neutral", "skipped"}
CRON_TRIGGERS = {"cron", "schedule"}


@dataclass
class MergeDecision:
    merge: bool
    reasons: list[str] = field(default_factory=list)  # blockers when merge is False, basis when True


def _bumped_deps(pr: dict[str, Any]) -> list[str]:
    m = re.match(r"(?i)\s*(?:chore(?:\([^)]*\))?:\s*)?bump\s+(\S+)", pr.get("title", ""))
    return [m.group(1)] if m else []


def merge_policy(pr: dict[str, Any], factory_cfg: dict[str, Any], plan_files: list[str] | None,
                 is_dependabot: bool, trigger: str) -> MergeDecision:
    blockers: list[str] = []
    files = pr.get("files") or []
    paths = [f["path"] for f in files]
    limits = factory_cfg.get("limits") or {}
    max_lines = int(limits.get("max_diff_lines", 300))
    max_files = int(limits.get("max_diff_files", 8))

    if is_dependabot and trigger not in CRON_TRIGGERS:
        blockers.append("dependabot PRs are merged from the cron trigger only")
    if pr.get("isDraft"):
        blockers.append("PR is a draft")
    if pr.get("mergeable") == "CONFLICTING":
        blockers.append("PR has merge conflicts")

    checks = pr.get("checks") or []
    if not checks:
        blockers.append("no CI checks reported")
    elif not all(c.get("status", "").lower() == "completed" and (c.get("conclusion") or "").lower() in GREEN
                 for c in checks):
        bad = [c.get("name", "?") for c in checks
               if not (c.get("status", "").lower() == "completed" and (c.get("conclusion") or "").lower() in GREEN)]
        blockers.append(f"CI not green: {', '.join(bad)}")

    unresolved = [t for t in pr.get("reviewThreads") or [] if not t.get("isResolved")]
    if unresolved:
        blockers.append(f"{len(unresolved)} unresolved review thread(s)")

    head = pr.get("headRefName", "")
    factory_pr = head.startswith(("feat/", "fix/")) and not is_dependabot
    if factory_pr:
        if not re.search(r"(?i)\bcloses\s+#\d+", pr.get("body") or ""):
            blockers.append("PR body lacks 'Closes #N'")
        if plan_files is None:
            blockers.append("no plan file list available for factory PR")
        else:
            norm = {p.lstrip("./") for p in plan_files}
            extra = [p for p in paths if p not in norm]
            if extra:
                blockers.append(f"files outside plan: {', '.join(extra[:5])}")

    lines = sum(int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in files)
    if lines > max_lines:
        blockers.append(f"diff too large: {lines} lines > {max_lines}")
    if len(files) > max_files:
        blockers.append(f"too many files: {len(files)} > {max_files}")

    deps = _bumped_deps(pr) if is_dependabot else []
    # Dependency-only Dependabot bumps are dark only when the profile sets dark_dependency_bumps (see classify_autonomy).
    autonomy = classify_autonomy(files, factory_cfg, deps)
    human_approved = any(
        r.get("state") == "APPROVED" and not str(r.get("author", "")).endswith("[bot]")
        and r.get("author") != pr.get("author")
        for r in pr.get("reviews") or []
    )
    if autonomy != "dark" and not human_approved:
        blockers.append("autonomy:lit requires a human APPROVED review")

    if blockers:
        return MergeDecision(False, blockers)
    return MergeDecision(True, ["autonomy:dark" if autonomy == "dark" else "human approved"])
