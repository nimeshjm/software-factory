"""File tools (read/list/search/edit), all confined to WORKSPACE."""

from __future__ import annotations

import os
import re
from pathlib import Path

from strands import tool

from .profile import get_profile
from .workspace import PathEscape, rel, resolve_in_workspace, workspace_root

MAX_READ_CHARS = 60_000
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}


def _skip_dirs() -> set[str]:
    return SKIP_DIRS | set(get_profile().toolchain.ignore_dirs)


def _err(msg: str) -> str:
    return f"error: {msg}"


@tool
def read_file(path: str, start_line: int = 1, max_lines: int = 800) -> str:
    """Read a text file from the workspace with line numbers.

    Args:
        path: File path relative to the workspace root.
        start_line: 1-based first line to return.
        max_lines: Maximum number of lines to return.
    """
    try:
        p = resolve_in_workspace(path)
    except PathEscape as e:
        return _err(str(e))
    if not p.is_file():
        return _err(f"not a file: {path}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return _err(str(e))
    lines = text.splitlines()
    start = max(1, int(start_line))
    chunk = lines[start - 1 : start - 1 + max(1, int(max_lines))]
    out = "\n".join(f"{i}\t{line}" for i, line in enumerate(chunk, start))
    if len(out) > MAX_READ_CHARS:
        out = out[:MAX_READ_CHARS] + "\n[truncated]"
    suffix = ""
    if start - 1 + len(chunk) < len(lines):
        suffix = f"\n[{len(lines)} lines total; continue with start_line={start + len(chunk)}]"
    return out + suffix


@tool
def list_files(path: str = ".", max_entries: int = 200) -> str:
    """List files under a workspace directory (recursive, skipping VCS, dependency and build directories).

    Args:
        path: Directory relative to the workspace root.
        max_entries: Maximum number of entries to return.
    """
    try:
        root = resolve_in_workspace(path)
    except PathEscape as e:
        return _err(str(e))
    if not root.is_dir():
        return _err(f"not a directory: {path}")
    ws = workspace_root()
    entries: list[str] = []
    limit = max(1, int(max_entries))
    skip = _skip_dirs()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            entries.append(rel(Path(dirpath) / name, ws))
            if len(entries) >= limit:
                return "\n".join(entries) + f"\n[truncated at {limit} entries]"
    return "\n".join(entries) if entries else "(empty)"


@tool
def search_files(pattern: str, path_glob: str = "**/*") -> str:
    """Search file contents with a regular expression (ripgrep-like).

    Args:
        pattern: Python regular expression to search for.
        path_glob: Glob (relative to the workspace) restricting which files are searched, e.g. 'src/**/*.py'.
    """
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return _err(f"bad regex: {e}")
    if ".." in Path(path_glob).parts or Path(path_glob).is_absolute():
        return _err("path_glob must be relative and without '..'")
    ws = workspace_root()
    glob_rx = _glob_to_regex(path_glob)
    hits: list[str] = []
    skip = _skip_dirs()
    for dirpath, dirnames, filenames in os.walk(ws):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            fp = Path(dirpath) / name
            r = rel(fp, ws)
            if not glob_rx.match(r):
                continue
            try:
                if fp.stat().st_size > 1_000_000 or fp.is_symlink():
                    continue
                text = fp.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{r}:{n}:{line[:200]}")
                    if len(hits) >= 200:
                        return "\n".join(hits) + "\n[truncated at 200 matches]"
    return "\n".join(hits) if hits else "no matches"


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    from factory.policy.globs import glob_to_regex

    return glob_to_regex(glob)


@tool
def edit_file(
    path: str,
    command: str,
    file_text: str = "",
    old_str: str = "",
    new_str: str = "",
    insert_line: int = 0,
    insert_text: str = "",
) -> str:
    """Create or edit a file in the workspace.

    Args:
        path: File path relative to the workspace root.
        command: One of 'create' (needs file_text), 'str_replace' (needs old_str and new_str; old_str must match exactly once), 'insert' (needs insert_line and insert_text; text goes after that line, 0 = top).
        file_text: Full file content for 'create'.
        old_str: Exact text to replace for 'str_replace'.
        new_str: Replacement text for 'str_replace'.
        insert_line: Line number after which to insert for 'insert'.
        insert_text: Text to insert for 'insert'.
    """
    try:
        p = resolve_in_workspace(path)
    except PathEscape as e:
        return _err(str(e))
    cmd = command.strip()
    if cmd == "create":
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(file_text, encoding="utf-8")
        return f"created {rel(p)} ({len(file_text)} chars)"
    if not p.is_file():
        return _err(f"not a file: {path}")
    text = p.read_text(encoding="utf-8")
    if cmd == "str_replace":
        count = text.count(old_str) if old_str else 0
        if count != 1:
            return _err(f"old_str must match exactly once, matched {count} times")
        p.write_text(text.replace(old_str, new_str, 1), encoding="utf-8")
        return f"edited {rel(p)}"
    if cmd == "insert":
        lines = text.splitlines(keepends=True)
        n = int(insert_line)
        if n < 0 or n > len(lines):
            return _err(f"insert_line out of range 0..{len(lines)}")
        ins = insert_text if insert_text.endswith("\n") else insert_text + "\n"
        if n > 0 and lines and not lines[n - 1].endswith("\n"):
            lines[n - 1] += "\n"
        lines.insert(n, ins)
        p.write_text("".join(lines), encoding="utf-8")
        return f"inserted into {rel(p)} after line {n}"
    return _err("command must be create, str_replace or insert")


def simulate_edit(current: str | None, command: str, args: dict) -> str | None:
    """Pure helper used by the tool guard to preview the result of an edit. None if not computable."""
    cmd = (command or "").strip()
    if cmd == "create":
        return args.get("file_text", "")
    if current is None:
        return None
    if cmd == "str_replace":
        old = args.get("old_str", "")
        if not old or current.count(old) != 1:
            return None
        return current.replace(old, args.get("new_str", ""), 1)
    if cmd == "insert":
        lines = current.splitlines(keepends=True)
        n = int(args.get("insert_line", 0) or 0)
        if n < 0 or n > len(lines):
            return None
        ins = args.get("insert_text", "")
        ins = ins if ins.endswith("\n") else ins + "\n"
        if n > 0 and lines and not lines[n - 1].endswith("\n"):
            lines[n - 1] += "\n"
        lines.insert(n, ins)
        return "".join(lines)
    return None


__all__ = ["read_file", "list_files", "search_files", "edit_file", "simulate_edit"]
