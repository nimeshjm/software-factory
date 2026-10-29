"""ToolCallLog: records every tool call (name, target, status, http_status) for result.json and telemetry."""

from __future__ import annotations

import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any

from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

from .output_guard import redact

MAX_CALLS = 200
TARGET_MAX = 200
BLOCKED_PREFIX = "[tool_guard] Blocked"
HTTP_RE = re.compile(r"\AHTTP (\d{3})\b")
SOURCE_RE = re.compile(r'<untrusted source="([^"]*)"')
PATH_TOOLS = {"read_file", "list_files", "edit_file"}


def _result_text(result: Any) -> str:
    try:
        parts = [c["text"] for c in (result or {}).get("content", []) if isinstance(c, dict) and "text" in c]
        return "\n".join(str(p) for p in parts)
    except Exception:  # noqa: BLE001
        return ""


def _target(name: str, inp: Any, text: str, status: str, http_status: int | None) -> str:
    inp = inp if isinstance(inp, dict) else {}
    if name == "fetch_url":
        if status == "ok" and http_status is not None:
            m = SOURCE_RE.search(text)
            if m:
                return m.group(1)
        return str(inp.get("url", ""))
    if name == "web_search":
        return str(inp.get("query", ""))
    if name in PATH_TOOLS:
        return str(inp.get("path", ""))
    if name == "search_files":
        return " ".join(str(inp[k]) for k in ("pattern", "path_glob") if inp.get(k))
    if name == "shell_in_workspace":
        return str(inp.get("command", ""))
    return ""


class ToolCallLog(HookProvider):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.after)

    def after(self, event: AfterToolCallEvent) -> None:
        try:
            self._record(event)
        except Exception:  # noqa: BLE001 - logging must never break a run
            pass

    def _record(self, event: AfterToolCallEvent) -> None:
        name = str(event.tool_use.get("name", ""))
        text = _result_text(event.result)
        if text.startswith(BLOCKED_PREFIX) or (event.cancel_message or "").startswith(BLOCKED_PREFIX):
            status = "blocked"
        elif text.startswith("error:"):
            status = "error"
        else:
            status = "ok"
        http_status = None
        if name == "fetch_url":
            m = HTTP_RE.match(text)
            http_status = int(m.group(1)) if m else None
        target = redact(_target(name, event.tool_use.get("input"), text, status, http_status))[:TARGET_MAX]
        entry = {"name": name, "target": target, "status": status, "http_status": http_status,
                 "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with self._lock:
            if len(self.calls) < MAX_CALLS:
                self.calls.append(entry)
        print(f"[tool] {name} {status} {http_status if http_status is not None else '-'} {target}", file=sys.stderr)
        try:
            from opentelemetry import trace

            attrs: dict[str, Any] = {"factory.tool.name": name, "factory.tool.target": target,
                                     "factory.tool.status": status}
            if http_status is not None:
                attrs["factory.tool.http_status"] = http_status
            # Strands ends the execute_tool span before AfterToolCallEvent fires, so the current span is the
            # enclosing cycle span; per-call attributes would overwrite each other, so only a span event is added.
            trace.get_current_span().add_event("factory.tool_call", attrs)
        except Exception:  # noqa: BLE001 - telemetry must never break a run
            pass
