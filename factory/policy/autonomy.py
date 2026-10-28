"""Light/dark classification."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from typing import Any

from .globs import match_glob


def _paths(files: Iterable[Any]) -> list[str]:
    return [f["path"] if isinstance(f, dict) else str(f) for f in files]


def classify_autonomy(files: Iterable[Any], factory_cfg: dict[str, Any],
                      changed_dependencies: Iterable[str] = ()) -> str:
    """'dark' iff every changed path matches dark_paths and no bumped dependency is in lit_dependencies."""
    paths = _paths(files)
    if not paths:
        return "lit"
    norm = [p[2:] if p.startswith("./") else p for p in paths]
    if any(p == "factory.yml" or p.startswith(".github/") for p in norm):
        return "lit"  # factory.yml and .github/** are never dark, whatever dark_paths says
    dark = factory_cfg.get("dark_paths") or []
    if not all(any(match_glob(p, g) for g in dark) for p in paths):
        return "lit"
    lit_deps = factory_cfg.get("lit_dependencies") or []
    for dep in changed_dependencies:
        if any(fnmatchcase(dep, pat) for pat in lit_deps):
            return "lit"
    return "dark"
