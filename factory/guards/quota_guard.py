"""QuotaGuard: per-run model-call cap plus throttle counting."""

from __future__ import annotations

from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent, HookProvider, HookRegistry
from strands.types.exceptions import ModelThrottledException

from .events import GuardLog


class QuotaGuard(HookProvider):
    def __init__(self, max_model_calls: int, log: GuardLog) -> None:
        self.max_model_calls = max_model_calls
        self.log = log
        self.calls = 0
        self.throttles = 0
        self.capped = False

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:  # noqa: ANN003
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        if self.calls >= self.max_model_calls:
            event.cancel = "[quota_guard] run model-call cap reached"
            if not self.capped:
                self.capped = True
                self.log.record("quota_guard", "run_call_cap", f"cap={self.max_model_calls}")
            return
        self.calls += 1

    def after_model(self, event: AfterModelCallEvent) -> None:
        if isinstance(event.exception, ModelThrottledException):
            self.throttles += 1

    def extra_call(self) -> None:
        """Count a request the model re-sent itself (daily-quota fallback) so the cap and the ledger see it."""
        self.calls += 1
