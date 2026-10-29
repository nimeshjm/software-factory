"""Maps tool names from factory.config.json to @tool objects."""

from __future__ import annotations

from typing import Any

from .fetch import fetch_url
from .files import edit_file, list_files, read_file, search_files
from .honeycomb import honeycomb_query
from .web_search import web_search
from .shell import git_commit, run_checks, shell_in_workspace

TOOL_REGISTRY: dict[str, Any] = {
    "read_file": read_file,
    "list_files": list_files,
    "search_files": search_files,
    "edit_file": edit_file,
    "shell_in_workspace": shell_in_workspace,
    "run_checks": run_checks,
    "git_commit": git_commit,
    "honeycomb_query": honeycomb_query,
    "fetch_url": fetch_url,
    "web_search": web_search,
}


def build_tools(names: list[str]) -> list[Any]:
    missing = [n for n in names if n not in TOOL_REGISTRY]
    if missing:
        raise KeyError(f"unknown tools in config: {missing}")
    return [TOOL_REGISTRY[n] for n in names]
