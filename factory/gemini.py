"""GeminiModel that also retries 503 UNAVAILABLE and falls back to the next model on a daily free-tier quota 429.

Strands maps RESOURCE_EXHAUSTED/UNAVAILABLE to ModelThrottledException only for google-genai ClientError (4xx).
A 503 arrives as ServerError, so the run died on the first overloaded call instead of backing off.
A 429 arrives as ModelThrottledException (raised `from` the ClientError, strands/models/gemini.py), so the
daily-quota check reads the QuotaFailure details off `__cause__`; backing off for hours would only fail the run.
A per-minute 429 is waited out on the same model (RetryInfo `retryDelay`, capped) before it counts toward a switch:
switching after strands' 4 s retry moved runs onto the last fallback, whose free-tier daily quota is the smallest.
A billing block (402 Payment Required when the prepay balance is zero, or a 429 naming a spend cap or depleted
prepayment credits) stops the run as ModelBillingError: every model on the key fails the same way, so neither a
wait nor a fallback can help, and strands must not retry it.
A 503 "high demand" (UNAVAILABLE) is also waited out on the same model (overload_wait_s doubling, capped) before
it counts toward a switch: on 2026-11-24 Google shed free-tier traffic and two quick 503s per model walked both
runs through the free chain and onto the paid key within 70 s.
Key tiers: with a free-tier key and a billing-linked (paid) key, the run walks the whole model chain on the free
key first and only then the same chain on the paid key. Only a daily-quota 429 moves a run onto a paid tier: when
the free chain is used up by transient failures (overload, per-minute throttles, stalls, dropped connections) the
run stops with ModelOverloadedError and the control plane retries it after the failure backoff. Paid requests are priced from their usage metadata and
stop (ModelPaidBudgetError) once the run's paid allowance, handed out by the control plane, is spent.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from google.genai import errors as genai_errors
from opentelemetry import trace
from strands.models.gemini import GeminiModel
from strands.types.exceptions import ModelThrottledException

RETRYABLE_SERVER_CODES = frozenset({503})
BILLING_MARKERS = ("prepayment credits are depleted", "prepayment credits depleted", "spend cap", "spending cap")


class ModelBillingError(Exception):
    """The key's project cannot pay for the request: prepay balance at zero or a monthly spend cap reached."""


class ModelOverloadedError(Exception):
    """Every free model failed transiently (overloaded, throttled, stalled); the paid key is kept for daily-quota exhaustion."""


class ModelPaidBudgetError(Exception):
    """The run's paid allowance (its share of today's paid budget) is spent; no free model is left to use."""


@dataclass(frozen=True)
class KeyTier:
    name: str  # "free" or "paid" (or "default" for a single key on a free project)
    api_key: str
    paid: bool


# USD per 1M tokens: {"input": x, "output": y}. Unknown models are priced at the dearest listed one.
Prices = Mapping[str, Mapping[str, float]]


def request_cost_usd(model_id: str, usage: Mapping[str, Any], prices: Prices) -> float:
    """Estimated cost of one request from strands usage (inputTokens, outputTokens incl. thinking); cache reads at full price."""
    if prices:
        price = prices.get(model_id) or max(prices.values(), key=lambda p: (p["input"], p["output"]))
    else:
        price = {"input": 0.0, "output": 0.0}
    return (int(usage.get("inputTokens", 0)) * float(price["input"])
            + int(usage.get("outputTokens", 0)) * float(price["output"])) / 1_000_000


def is_billing_error(error: BaseException) -> bool:
    """True for a 402, or any error whose message or details name a spend cap or depleted prepayment credits."""
    for e in (error, error.__cause__):
        if e is None:
            continue
        if isinstance(e, genai_errors.APIError) and e.code == 402:
            return True
        text = f"{e} {getattr(e, 'details', '') or ''}".lower()
        if any(m in text for m in BILLING_MARKERS):
            return True
    return False


def _billing_error(error: BaseException) -> ModelBillingError:
    print(f"[model] billing blocked: {str(error)[:300]}", file=sys.stderr, flush=True)
    return ModelBillingError(f"Gemini billing blocked (prepay balance or spend cap): {str(error)[:500]}")


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


def _retry_delays(obj: Any) -> list[str]:
    """Every RetryInfo `retryDelay` value (e.g. "23s") anywhere in a details payload."""
    if isinstance(obj, dict):
        found = [str(obj["retryDelay"])] if "retryDelay" in obj else []
        return found + [d for v in obj.values() for d in _retry_delays(v)]
    if isinstance(obj, list):
        return [d for v in obj for d in _retry_delays(v)]
    return []


def throttle_delay(error: BaseException, attempt: int, cap_s: float) -> float:
    """Seconds to wait before resending a per-minute throttle: the server's retryDelay (plus 1 s), else 15 s doubling; capped."""
    chain = [error, error.__cause__]
    for e in chain:
        if e is None:
            continue
        for d in _retry_delays(getattr(e, "details", None)):
            m = re.fullmatch(r"\s*([0-9.]+)s\s*", d)
            if m:
                return min(float(m.group(1)) + 1.0, cap_s)
        m = re.search(r"retry in ([0-9.]+)s", str(e))
        if m:
            return min(float(m.group(1)) + 1.0, cap_s)
    return min(15.0 * 2 ** attempt, cap_s)


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
                 first_event_timeout_s: float = 150, unresponsive_switch_after: int = 2,
                 throttle_waits: int = 3, throttle_wait_max_s: float = 65,
                 overload_waits: int = 3, overload_wait_s: float = 20, overload_wait_max_s: float = 60,
                 key_tiers: list[KeyTier] | None = None, paid_allowance_usd: float = 0.0,
                 prices: Prices | None = None, paid_model_ids: tuple[str, ...] | list[str] = (),
                 **kwargs: Any) -> None:
        self.key_tiers = list(key_tiers or [])
        if self.key_tiers:
            kwargs["client_args"] = {"api_key": self.key_tiers[0].api_key}
        super().__init__(*args, **kwargs)
        self.tier_index = 0
        self.chain = [self.config["model_id"], *fallback_model_ids]  # walked once per free/default key tier
        self.paid_chain = list(paid_model_ids) or list(self.chain)  # walked on a paid key tier
        start = self.chain_for(self.key_tiers[0]) if self.key_tiers else self.chain
        if start[0] != self.config["model_id"]:
            self.update_config(model_id=start[0])
        self.fallback_model_ids = list(start[1:])
        self.paid_allowance_usd = float(paid_allowance_usd)
        self.prices: Prices = prices or {}
        self.paid_cost_usd = 0.0
        self.tiers_used: list[str] = [self.key_tiers[0].name] if self.key_tiers else []
        self.first_event_timeout_s = first_event_timeout_s
        self.unresponsive_switch_after = unresponsive_switch_after
        self.unresponsive_calls = 0
        self.throttle_waits = throttle_waits
        self.throttle_wait_max_s = throttle_wait_max_s
        self.throttle_waits_done = 0  # per-minute waits since the last content event; reset on a switch
        self.overload_waits = overload_waits
        self.overload_wait_s = overload_wait_s
        self.overload_wait_max_s = overload_wait_max_s
        self.overload_waits_done = 0  # 503 waits since the last content event; reset on a switch
        self.sleep: Callable[[float], Any] = asyncio.sleep
        self.on_throttle_wait: Callable[[], None] | None = None
        self.model_ids_used: list[str] = [self.config["model_id"]]
        self.model_switches: list[dict[str, str]] = []
        self.on_switch: Callable[[str, str], None] | None = None

    def chain_for(self, tier: KeyTier) -> list[str]:
        return self.paid_chain if tier.paid else self.chain

    @property
    def tier(self) -> KeyTier | None:
        return self.key_tiers[self.tier_index] if self.key_tiers else None

    def _paid_spent(self) -> bool:
        return self.paid_cost_usd >= self.paid_allowance_usd

    def _next_tier(self, transient: bool = False) -> int | None:
        """Index of the next key tier that can still be used (a paid one only while allowance remains, and never
        for a transient failure: the paid key is for daily-quota exhaustion, not for a busy free tier)."""
        for i in range(self.tier_index + 1, len(self.key_tiers)):
            paid = self.key_tiers[i].paid
            if paid and transient:
                continue
            if not (paid and self._paid_spent()):
                return i
        return None

    def _next_paid_blocked(self) -> bool:
        """True when a later paid tier exists but its allowance is spent (so the run stops on budget, not quota)."""
        return any(t.paid for t in self.key_tiers[self.tier_index + 1:]) and self._paid_spent()

    def _can_fall_back(self, transient: bool = False) -> bool:
        return bool(self.fallback_model_ids) or self._next_tier(transient) is not None

    def _paid_held_back(self) -> bool:
        """True when a transient failure has used up this (unpaid) tier and only a paid tier is left."""
        tier = self.tier
        return (tier is not None and not tier.paid and not self._can_fall_back(transient=True)
                and self._can_fall_back())

    def _switch_tier(self, index: int, reason: str) -> None:
        old_tier, new = self.tier, self.key_tiers[index]
        self.tier_index = index
        self.client_args = {"api_key": new.api_key}
        chain = self.chain_for(new)
        self.fallback_model_ids = list(chain[1:])
        if new.name not in self.tiers_used:
            self.tiers_used.append(new.name)
        print(f"[model] {old_tier.name if old_tier else '?'} key exhausted ({reason}); switching to the {new.name} key",
              file=sys.stderr, flush=True)
        self._switch_model(reason=f"{new.name}_key:{reason}", new=chain[0])

    def _fall_back(self, reason: str = "daily_quota", transient: bool = False) -> None:
        """Next model on this key, else the chain's primary on the next usable key tier."""
        if self.fallback_model_ids:
            self._switch_model(reason=reason)
            return
        index = self._next_tier(transient)
        assert index is not None, "_fall_back without _can_fall_back"
        self._switch_tier(index, reason)

    def _switch_model(self, reason: str = "daily_quota", new: str | None = None) -> None:
        old = self.config["model_id"]
        new = new if new is not None else self.fallback_model_ids.pop(0)
        if reason == "daily_quota":
            print(f"[model] {old} daily quota exhausted; switching to {new}", file=sys.stderr, flush=True)
        elif reason.split(":", 1)[0].endswith("_key"):
            pass  # _switch_tier printed the key switch
        else:
            detail = reason.split(":", 1)[-1]
            print(f"[model] {old} unresponsive ({detail}); switching to {new}", file=sys.stderr, flush=True)
        self.update_config(model_id=new)
        self.throttle_waits_done = 0
        self.overload_waits_done = 0
        if new not in self.model_ids_used:
            self.model_ids_used.append(new)
        self.model_switches.append({"from": old, "to": new, "reason": reason})
        span = trace.get_current_span()
        span.add_event("factory.model_switch", {"factory.model.from": old, "factory.model.to": new,
                                                "factory.model.reason": reason})
        span.set_attribute("factory.model_id", new)
        if self.on_switch:
            self.on_switch(old, new)

    def _record_retry(self, reason: str, attempt: int) -> None:
        trace.get_current_span().add_event("factory.model_retry", {
            "factory.model_id": self.config["model_id"], "factory.retry.reason": reason,
            "factory.retry.attempt": attempt})

    def _unresponsive(self, detail: str) -> bool:
        """Count one unresponsive call; True when the threshold is hit and a fallback remains (model already switched).

        Raises ModelOverloadedError when the threshold is hit on an unpaid tier whose only way on is a paid tier."""
        self.unresponsive_calls += 1
        self._record_retry(detail, self.unresponsive_calls)
        if self.unresponsive_calls < self.unresponsive_switch_after:
            return False
        if self._can_fall_back(transient=True):
            self._fall_back(reason=f"unresponsive:{detail}", transient=True)
            self.unresponsive_calls = 0
            return True
        if self._paid_held_back():
            print(f"[model] free models unresponsive ({detail}); not using the paid key for a transient failure",
                  file=sys.stderr, flush=True)
            raise ModelOverloadedError(
                f"every free model failed transiently (last: {self.config['model_id']} {detail}); "
                f"the paid key is kept for daily-quota exhaustion, retry later")
        return False

    async def _overload_wait(self) -> bool:
        """Wait out a 503 on the same model; False once this model's waits are used up."""
        if self.overload_waits_done >= self.overload_waits:
            return False
        delay = min(self.overload_wait_s * (2 ** self.overload_waits_done), self.overload_wait_max_s)
        self.overload_waits_done += 1
        self._record_retry("overload_wait", self.overload_waits_done)
        print(f"[model] {self.config['model_id']} overloaded (503); waiting {delay:.0f}s",
              file=sys.stderr, flush=True)
        if self.on_throttle_wait:
            self.on_throttle_wait()
        await self.sleep(delay)
        return True

    def _check_paid_budget(self) -> None:
        tier = self.tier
        if tier is not None and tier.paid and self._paid_spent():
            raise ModelPaidBudgetError(f"paid allowance spent: ${self.paid_cost_usd:.4f} of ${self.paid_allowance_usd:.4f} "
                                       f"for this run (control plane daily paid budget)")

    def _account(self, event: Any) -> None:
        tier = self.tier
        if tier is not None and tier.paid and isinstance(event, dict) and "metadata" in event:
            usage = (event["metadata"] or {}).get("usage") or {}
            self.paid_cost_usd += request_cost_usd(self.config["model_id"], usage, self.prices)

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        while True:
            self._check_paid_budget()
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
                    self.throttle_waits_done = 0
                    self.overload_waits_done = 0
                    self._account(event)
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
                if is_billing_error(error):
                    raise _billing_error(error) from error
                if yielded:
                    raise
                if is_daily_quota_error(error):
                    self._record_retry("daily_quota", 1)
                    if not self._can_fall_back():
                        if self._next_paid_blocked():
                            raise ModelPaidBudgetError(
                                f"free models exhausted and the paid allowance is spent "
                                f"(${self.paid_cost_usd:.4f} of ${self.paid_allowance_usd:.4f})") from error
                        raise
                    self._fall_back()
                    continue
                if self.throttle_waits_done < self.throttle_waits:
                    delay = throttle_delay(error, self.throttle_waits_done, self.throttle_wait_max_s)
                    self.throttle_waits_done += 1
                    self._record_retry("throttled_wait", self.throttle_waits_done)
                    print(f"[model] {self.config['model_id']} throttled (per minute); waiting {delay:.0f}s",
                          file=sys.stderr, flush=True)
                    if self.on_throttle_wait:
                        self.on_throttle_wait()
                    await self.sleep(delay)
                    continue
                if self._unresponsive("throttled"):
                    continue
                raise
            except genai_errors.ClientError as error:
                # strands re-raises 4xx it does not map (402 among them) as the raw ClientError
                if is_billing_error(error):
                    raise _billing_error(error) from error
                raise
            except genai_errors.ServerError as error:
                if not is_retryable_server_error(error):
                    raise
                if not yielded and await self._overload_wait():
                    continue
                if not yielded and self._unresponsive("server_error"):
                    continue
                raise ModelThrottledException(error.message or str(error)) from error
            except httpx.TransportError as error:
                if yielded:
                    raise
                if self._unresponsive("transport_error"):
                    continue
                raise ModelThrottledException(f"{type(error).__name__}: {error}") from error
