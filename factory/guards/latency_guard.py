"""LatencyGuard: soft nudge at 80% of the time budget, hard cancel at 100%."""

from __future__ import annotations

import threading
from typing import Any

from strands.hooks import AfterInvocationEvent, AfterToolsEvent, BeforeInvocationEvent, HookProvider, HookRegistry

from .events import GuardLog

NUDGE = "[latency_guard] time budget nearly spent, finish now with the proposal"


class LatencyGuard(HookProvider):
    def __init__(self, timeout_s: float, log: GuardLog, soft_fraction: float = 0.8) -> None:
        self.timeout_s = timeout_s
        self.soft_s = timeout_s * soft_fraction
        self.log = log
        self.soft_flag = threading.Event()
        self.nudged = False
        self.hard_fired = False
        self._timers: list[threading.Timer] = []
        self._started = False

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:  # noqa: ANN003
        registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
        registry.add_callback(AfterToolsEvent, self.on_after_tools)

    def on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        self.start(event.agent)

    def start(self, agent: Any) -> None:
        """Start the timers once per run (a second invocation shares the same budget)."""
        if self._started:
            return
        self._started = True
        soft = threading.Timer(self.soft_s, self.soft_flag.set)
        hard = threading.Timer(self.timeout_s, self._hard, args=(agent,))
        for t in (soft, hard):
            t.daemon = True
            t.start()
            self._timers.append(t)

    def _hard(self, agent: Any) -> None:
        self.hard_fired = True
        self.log.record("latency_guard", "hard_timeout", f"timeout_s={self.timeout_s}")
        agent.cancel()

    def on_after_tools(self, event: AfterToolsEvent) -> None:
        if self.soft_flag.is_set() and not self.nudged:
            self.nudged = True
            event.end_turn = NUDGE
            self.log.record("latency_guard", "soft_nudge", f"soft_s={self.soft_s}")

    def stop(self) -> None:
        for t in self._timers:
            t.cancel()
