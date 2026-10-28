"""Shared guard event log; every guard records here and the run writes it to result.json."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any


class GuardLog:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, guard: str, rule: str, detail: str = "") -> dict[str, Any]:
        ev = {
            "guard": guard,
            "rule": rule,
            "detail": detail[:500],
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock:
            self.events.append(ev)
        try:
            from opentelemetry import trace

            trace.get_current_span().add_event(
                f"{guard}.blocked" if guard == "tool_guard" else f"{guard}.event",
                {"guard": guard, "rule": rule, "detail": ev["detail"]},
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a guard
            pass
        return ev

    def has(self, guard: str, rule: str | None = None) -> bool:
        return any(e["guard"] == guard and (rule is None or e["rule"] == rule) for e in self.events)
