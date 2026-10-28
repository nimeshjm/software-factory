"""Process-wide target profile used by tools and guards. Defaults to a baseline ``TargetProfile()``."""

from __future__ import annotations

from factory.schemas.target_profile import TargetProfile

_PROFILE: TargetProfile | None = None


def set_profile(p: TargetProfile | None) -> None:
    global _PROFILE
    _PROFILE = p


def get_profile() -> TargetProfile:
    return _PROFILE if _PROFILE is not None else TargetProfile()
