"""Workspace path confinement shared by tools and guards."""

from __future__ import annotations

import os
from pathlib import Path


class PathEscape(ValueError):
    """Raised when a path resolves outside WORKSPACE."""


def workspace_root() -> Path:
    ws = os.environ.get("WORKSPACE")
    if not ws:
        raise RuntimeError("WORKSPACE is not set")
    return Path(ws).resolve()


def resolve_in_workspace(path: str, root: Path | None = None) -> Path:
    """Resolve ``path`` (relative to the workspace, or absolute) under the workspace.

    Rejects ``..`` components outright and resolves symlinks, so a link pointing
    outside the workspace is refused too.
    """
    root = (root or workspace_root()).resolve()
    if path is None or path == "" or "\x00" in path:
        raise PathEscape("empty or invalid path")
    p = Path(path)
    if ".." in p.parts:
        raise PathEscape(f"'..' is not allowed in paths: {path}")
    candidate = p if p.is_absolute() else root / p
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathEscape(f"path resolves outside the workspace: {path}") from None
    return resolved


def rel(path: Path, root: Path | None = None) -> str:
    root = (root or workspace_root()).resolve()
    try:
        return path.resolve().relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)


def clean_env() -> dict[str, str]:
    """Environment for subprocesses: PATH, HOME, CI plus the profile's env_prefixes. No secrets.

    Names starting with a NEVER_EXPORT_PREFIXES entry are always dropped, whatever the profile says.
    """
    from factory.guards.tool_guard import NEVER_EXPORT_PREFIXES

    from .profile import get_profile

    prefixes = tuple(x for x in get_profile().toolchain.env_prefixes if x)
    keep = {}
    for k, v in os.environ.items():
        if k.startswith(NEVER_EXPORT_PREFIXES):
            continue
        if k in ("PATH", "HOME", "CI") or (prefixes and k.startswith(prefixes)):
            keep[k] = v
    keep.setdefault("CI", "true")
    return keep
