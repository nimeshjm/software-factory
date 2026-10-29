"""GeminiModel that also retries 503 UNAVAILABLE and falls back to the next model on a daily free-tier quota 429.

Strands maps RESOURCE_EXHAUSTED/UNAVAILABLE to ModelThrottledException only for google-genai ClientError (4xx).
A 503 arrives as ServerError, so the run died on the first overloaded call instead of backing off.
A 429 arrives as ModelThrottledException (raised `from` the ClientError, strands/models/gemini.py), so the
daily-quota check reads the QuotaFailure details off `__cause__`; backing off for hours would only fail the run.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator, Callable
from typing import Any

from google.genai import errors as genai_errors
from opentelemetry import trace
from strands.models.gemini import GeminiModel
from strands.types.exceptions import ModelThrottledException

RETRYABLE_SERVER_CODES = frozenset({503})


def is_retryable_server_error(error: BaseException) -> bool:
    return isinstance(error, genai_errors.ServerError) and (
        error.code in RETRYABLE_SERVER_CODES or error.status == "UNAVAILABLE")


def _quota_ids(obj: Any) -> list[str]:
    """Every `quotaId` value anywhere in a QuotaFailure-style details payload."""
    if isinstance(obj, dict):
        found = [str(obj["quotaId"])] if "quotaId" in obj else []
        return found + [q for v in obj.values() for q in _quota_ids(v)]
    if isinstance(obj, list):
        return [q for v in obj for q in _quota_ids(v)]
    return []


def is_daily_quota_error(error: BaseException) -> bool:
    """True for a 429 whose violated quota is per day (quotaId contains PerDay); per-minute ones are plain throttles."""
    chain = [error, error.__cause__]
    ids = [q for e in chain if e is not None for q in _quota_ids(getattr(e, "details", None))]
    if ids:
        return any("PerDay" in q for q in ids)
    return any(e is not None and "per day limit exceeded" in str(e).lower() for e in chain)


class FactoryGeminiModel(GeminiModel):
    def __init__(self, *args: Any, fallback_model_ids: tuple[str, ...] | list[str] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fallback_model_ids = list(fallback_model_ids)
        self.model_ids_used: list[str] = [self.config["model_id"]]
        self.on_switch: Callable[[str, str], None] | None = None

    def _switch_model(self) -> None:
        old, new = self.config["model_id"], self.fallback_model_ids.pop(0)
        print(f"[model] {old} daily quota exhausted; switching to {new}", file=sys.stderr, flush=True)
        self.update_config(model_id=new)
        if new not in self.model_ids_used:
            self.model_ids_used.append(new)
        span = trace.get_current_span()
        span.add_event("factory.model_switch", {"factory.model.from": old, "factory.model.to": new})
        span.set_attribute("factory.model_id", new)
        if self.on_switch:
            self.on_switch(old, new)

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        while True:
            # strands yields messageStart before the streamed response is read, and a 429 surfaces only on
            # that read, so messageStart is held back until real content follows; a switch can still resend.
            held, yielded = None, False
            try:
                async for event in super().stream(*args, **kwargs):
                    if not yielded and held is None and "messageStart" in event:
                        held = event
                        continue
                    if held is not None:
                        yield held
                        held = None
                    yielded = True
                    yield event
                if held is not None:
                    yield held
                return
            except ModelThrottledException as error:
                if yielded or not self.fallback_model_ids or not is_daily_quota_error(error):
                    raise
                self._switch_model()
            except genai_errors.ServerError as error:
                if is_retryable_server_error(error):
                    raise ModelThrottledException(error.message or str(error)) from error
                raise
