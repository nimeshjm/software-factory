"""Plan hold. Pure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..schemas.target_profile import PlanReview, TargetProfile

APPROVED = "plan:approved"


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


def plan_gate(labels: list[str], profile: dict[str, Any] | TargetProfile | None, needs_human: bool) -> PlanGateDecision:
    """Hold after the plan when it needs a human, or a hold_on label is set without plan:approved."""
    reasons: list[str] = ["needs_human"] if needs_human else []
    if APPROVED not in labels:
        reasons += [f"label:{x}" for x in _hold_on(profile) if x in labels]
    return PlanGateDecision(bool(reasons), reasons)
