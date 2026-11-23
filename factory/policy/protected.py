"""Protected path globs: the tool guard's compiled-in baseline plus the profile's additions. Pure."""

from __future__ import annotations

from typing import Any

from ..schemas.target_profile import TargetProfile


# The tool guard's baseline path globs (factory/guards/tool_guard.py BASELINE_PATH_RULES), kept here so the
# plan-gate CLI stays pure and does not import strands. tests/test_plan_gate.py asserts the two stay equal.
BASELINE_GLOBS: tuple[str, ...] = (".git", ".git/**", ".github/**", "factory.yml")


def protected_globs(profile: dict[str, Any] | TargetProfile | None) -> list[str]:
    """Baseline globs (mirrors the tool guard) followed by profile.protected.paths."""
    globs = list(BASELINE_GLOBS)
    if isinstance(profile, TargetProfile):
        extra = list(profile.protected.paths)
    else:
        section = (profile or {}).get("protected")
        extra = [str(x) for x in (section.get("paths") or [])] if isinstance(section, dict) else []
    return globs + [g for g in extra if g not in globs]
