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
    """'dark' iff every changed path matches dark_paths and no bumped dependency is in lit_dependencies.

    With `dark_dependency_bumps: true`, a dependency bump (changed_dependencies non-empty) whose every path
    matches `toolchain.dependency_files` is also dark, unless a bumped dependency is in lit_dependencies.
    """
    paths = _paths(files)
    if not paths:
        return "lit"
    norm = [p[2:] if p.startswith("./") else p for p in paths]
    if any(p == "factory.yml" or p.startswith(".github/") for p in norm):
        return "lit"  # factory.yml and .github/** are never dark, whatever dark_paths says
    lit_deps = factory_cfg.get("lit_dependencies") or []
    deps = list(changed_dependencies)
    if any(fnmatchcase(dep, pat) for dep in deps for pat in lit_deps):
        return "lit"
    dep_globs = ((factory_cfg.get("toolchain") or {}).get("dependency_files")) or []
    if deps and factory_cfg.get("dark_dependency_bumps") and dep_globs \
            and all(any(match_glob(p, g) for g in dep_globs) for p in norm):
        return "dark"  # dependency-only bump; the merge policy still requires green CI
    dark = factory_cfg.get("dark_paths") or []
    if not all(any(match_glob(p, g) for g in dark) for p in paths):
        return "lit"
    return "dark"
