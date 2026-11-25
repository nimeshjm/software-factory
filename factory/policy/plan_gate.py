"""Plan hold. Pure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..schemas.target_profile import PlanReview, TargetProfile
from .globs import match_glob
from .protected import protected_globs

APPROVED = "plan:approved"
SPIKE = "spike"
EMPTY_PLAN = "empty_plan"


@dataclass
class PlanGateDecision:
    hold: bool
    reasons: list[str] = field(default_factory=list)


def _hold_on(profile: dict[str, Any] | TargetProfile | None) -> list[str]:
    if isinstance(profile, TargetProfile):
        return list(profile.plan_review.hold_on)
    section = (profile or {}).get("plan_review")
    if isinstance(section, dict) and section.get("hold_on") is not None:
        return [str(x) for x in section["hold_on"]]
    return list(PlanReview().hold_on)


def plan_gate(labels: list[str], profile: dict[str, Any] | TargetProfile | None, needs_human: bool,
              files: list[str] | None = None) -> PlanGateDecision:
    """Hold after the plan when it needs a human, names a protected path, lists no files (`empty_plan`, not for a
    spike, whose output is findings), or a hold_on label is set without plan:approved.

    `files=None` means the caller did not pass the plan's files (no protected or empty check); `[]` is an empty plan:
    approving one only runs an implement with nothing to change (#119 and #120, 2026-11-25)."""
    reasons: list[str] = ["needs_human"] if needs_human else []
    globs = protected_globs(profile)
    reasons += [f"protected:{f}" for f in (files or []) if any(match_glob(str(f), g) for g in globs)]
    if files is not None and not files and SPIKE not in labels:
        reasons.append(EMPTY_PLAN)
    if APPROVED not in labels:
        reasons += [f"label:{x}" for x in _hold_on(profile) if x in labels]
    return PlanGateDecision(bool(reasons), reasons)
