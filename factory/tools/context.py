"""read_context: read one of the target's context files that the task message listed instead of inlining.

The files are the pipeline's copies under `inputs/context/`, taken from the target's default branch and never
from a PR head, so a PR cannot change the conventions the agents read (the same files that would otherwise be
inlined). Names are basenames; nothing outside `inputs/context/` is reachable.
"""

from __future__ import annotations

from pathlib import Path

from strands import tool

MAX_CONTEXT_READ_CHARS = 20_000
_CONTEXT_DIR: Path | None = None


def set_context_dir(path: str | Path | None) -> None:
    global _CONTEXT_DIR
    _CONTEXT_DIR = Path(path) if path is not None else None


def context_names() -> list[str]:
    if _CONTEXT_DIR is None or not _CONTEXT_DIR.is_dir():
        return []
    return sorted(f.name for f in _CONTEXT_DIR.iterdir() if f.is_file())


@tool
def read_context(name: str) -> str:
    """Read a target context file (conventions, architecture, product spec) listed in the task message.

    Args:
        name: The file name exactly as listed under "Context files not inlined", e.g. "architecture.md".
    """
    names = context_names()
    if name not in names:
        return f"error: unknown context file {name!r}; available: {', '.join(names) or 'none'}"
    text = (_CONTEXT_DIR / name).read_text(encoding="utf-8", errors="replace")  # type: ignore[operator]
    if len(text) > MAX_CONTEXT_READ_CHARS:
        text = text[:MAX_CONTEXT_READ_CHARS] + "\n[truncated]"
    return text
