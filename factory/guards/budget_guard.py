"""BudgetGuard: soft nudge when tokens, turns or model calls near their caps, so the run can still finish."""

from __future__ import annotations

from typing import Any

from strands.hooks import AfterToolsEvent, HookProvider, HookRegistry

from .events import GuardLog
from .quota_guard import QuotaGuard

NUDGE = "[budget_guard] budget nearly spent, finish now with the proposal"


class BudgetGuard(HookProvider):
    def __init__(self, limits: dict[str, int], max_model_calls: int, quota: QuotaGuard, log: GuardLog,
                 soft_fraction: float = 0.85, finalize_reserve: int = 3) -> None:
        self.quota = quota
        self.log = log
        self.nudged = False
        self.tokens_cap = int(soft_fraction * limits["total_tokens"]) if limits.get("total_tokens") else None
        self.turns_cap = int(soft_fraction * limits["turns"]) if limits.get("turns") else None
        self.calls_cap = (max(1, min(int(soft_fraction * max_model_calls), max_model_calls - finalize_reserve))
                          if max_model_calls else None)

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:  # noqa: ANN003
        registry.add_callback(AfterToolsEvent, self.on_after_tools)

    def on_after_tools(self, event: AfterToolsEvent) -> None:
        if self.nudged:
            return
        try:
            tokens, turns = self._usage(event)
            checks = [("tokens", tokens, self.tokens_cap), ("turns", turns, self.turns_cap),
                      ("calls", self.quota.calls, self.calls_cap)]
            for kind, used, cap in checks:
                if cap is not None and used >= cap:
                    self.nudged = True
                    event.end_turn = NUDGE
                    self.log.record("budget_guard", "soft_nudge", f"{kind}={used}/{cap}")
                    return
        except Exception:  # noqa: BLE001 - a hook must never break the run
            return

    @staticmethod
    def _usage(event: Any) -> tuple[int, int]:
        inv = getattr(getattr(event.agent, "event_loop_metrics", None), "latest_agent_invocation", None)
        if inv is None:
            return 0, 0
        usage = getattr(inv, "usage", None) or {}
        return int(usage.get("totalTokens", 0) or 0), len(getattr(inv, "cycles", None) or [])
