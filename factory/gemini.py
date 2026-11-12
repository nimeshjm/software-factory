"""GeminiModel that also retries 503 UNAVAILABLE and falls back to the next model on a daily free-tier quota 429.

Strands maps RESOURCE_EXHAUSTED/UNAVAILABLE to ModelThrottledException only for google-genai ClientError (4xx).
A 503 arrives as ServerError, so the run died on the first overloaded call instead of backing off.
A 429 arrives as ModelThrottledException (raised `from` the ClientError, strands/models/gemini.py), so the
daily-quota check reads the QuotaFailure details off `__cause__`; backing off for hours would only fail the run.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
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


def unresponsive_reason(error: BaseException) -> str | None:
    """Why a failed call counts toward switching models: a per-minute throttle, a 503, or a dropped connection."""
    if isinstance(error, ModelThrottledException):
        return None if is_daily_quota_error(error) else "throttled"
    if is_retryable_server_error(error):
        return "server_error"
    if isinstance(error, httpx.TransportError):
        return "transport_error"
    return None


class FactoryGeminiModel(GeminiModel):
    def __init__(self, *args: Any, fallback_model_ids: tuple[str, ...] | list[str] = (),
                 first_event_timeout_s: float = 150, unresponsive_switch_after: int = 2, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fallback_model_ids = list(fallback_model_ids)
        self.first_event_timeout_s = first_event_timeout_s
        self.unresponsive_switch_after = unresponsive_switch_after
        self.unresponsive_calls = 0
        self.model_ids_used: list[str] = [self.config["model_id"]]
        self.model_switches: list[dict[str, str]] = []
        self.on_switch: Callable[[str, str], None] | None = None

    def _switch_model(self, reason: str = "daily_quota") -> None:
        old, new = self.config["model_id"], self.fallback_model_ids.pop(0)
        if reason == "daily_quota":
            print(f"[model] {old} daily quota exhausted; switching to {new}", file=sys.stderr, flush=True)
        else:
            detail = reason.split(":", 1)[-1]
            print(f"[model] {old} unresponsive ({detail}); switching to {new}", file=sys.stderr, flush=True)
        self.update_config(model_id=new)
        if new not in self.model_ids_used:
            self.model_ids_used.append(new)
        self.model_switches.append({"from": old, "to": new, "reason": reason})
        span = trace.get_current_span()
        span.add_event("factory.model_switch", {"factory.model.from": old, "factory.model.to": new,
                                                "factory.model.reason": reason})
        span.set_attribute("factory.model_id", new)
        if self.on_switch:
            self.on_switch(old, new)

    def _unresponsive(self, detail: str) -> bool:
        """Count one unresponsive call; True when the threshold is hit and a fallback remains (model already switched)."""
        self.unresponsive_calls += 1
        if self.unresponsive_calls >= self.unresponsive_switch_after and self.fallback_model_ids:
            self._switch_model(reason=f"unresponsive:{detail}")
            self.unresponsive_calls = 0
            return True
        return False

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        while True:
            # strands yields messageStart before the streamed response is read, and a 429 surfaces only on
            # that read, so messageStart is held back until real content follows; a switch can still resend.
            held, yielded = None, False
            inner = super().stream(*args, **kwargs).__aiter__()
            try:
                while True:
                    try:
                        # a hung call yields nothing, so only the wait for the first content event has a deadline
                        if yielded:
                            event = await inner.__anext__()
                        else:
                            event = await asyncio.wait_for(inner.__anext__(), self.first_event_timeout_s)
                    except StopAsyncIteration:
                        break
                    if not yielded and held is None and "messageStart" in event:
                        held = event
                        continue
                    if held is not None:
                        yield held
                        held = None
                    yielded = True
                    self.unresponsive_calls = 0
                    yield event
                if held is not None:
                    yield held
                return
            except asyncio.TimeoutError:
                await inner.aclose()
                if self._unresponsive("stall"):
                    continue
                raise ModelThrottledException(f"no response within {self.first_event_timeout_s:g}s") from None
            except ModelThrottledException as error:
                if yielded:
                    raise
                if is_daily_quota_error(error):
                    if not self.fallback_model_ids:
                        raise
                    self._switch_model()
                    continue
                if self._unresponsive("throttled"):
                    continue
                raise
            except genai_errors.ServerError as error:
                if not is_retryable_server_error(error):
                    raise
                if not yielded and self._unresponsive("server_error"):
                    continue
                raise ModelThrottledException(error.message or str(error)) from error
            except httpx.TransportError as error:
                if yielded:
                    raise
                if self._unresponsive("transport_error"):
                    continue
                raise ModelThrottledException(f"{type(error).__name__}: {error}") from error
