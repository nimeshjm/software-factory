import asyncio
import json

import httpx
import pytest
from google.genai import errors as genai_errors
from strands.models.gemini import GeminiModel
from strands.types.exceptions import ModelThrottledException

from factory.gemini import (FactoryGeminiModel, ModelBillingError, is_billing_error, is_daily_quota_error,
                            is_retryable_server_error, throttle_delay, unresponsive_reason)


def _server_error(code: int, status: str) -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"error": {"code": code, "message": "high demand", "status": status}})


def _drain(model):
    async def go():
        return [e async for e in model.stream([])]
    return asyncio.run(go())


def _model(monkeypatch, exc):
    async def boom(self, *a, **k):
        raise exc
        yield  # pragma: no cover

    monkeypatch.setattr(GeminiModel, "stream", boom)
    return FactoryGeminiModel(client_args={"api_key": "x"}, model_id="m", overload_waits=0)


def test_503_becomes_throttled(monkeypatch):
    with pytest.raises(ModelThrottledException):
        _drain(_model(monkeypatch, _server_error(503, "UNAVAILABLE")))


def test_500_is_not_retried(monkeypatch):
    with pytest.raises(genai_errors.ServerError):
        _drain(_model(monkeypatch, _server_error(500, "INTERNAL")))


def test_classifier():
    assert is_retryable_server_error(_server_error(503, "UNAVAILABLE"))
    assert not is_retryable_server_error(_server_error(500, "INTERNAL"))
    assert not is_retryable_server_error(ValueError("x"))


def _quota_error(quota_id: str, message: str = "Quota exceeded", retry_delay: str | None = None) -> ModelThrottledException:
    extra = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}] if retry_delay else []
    details = {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]}, *extra]}}
    err = ModelThrottledException(message)
    err.__cause__ = genai_errors.ClientError(429, details)
    return err


DAILY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def test_is_daily_quota_error():
    assert is_daily_quota_error(_quota_error(DAILY))
    assert is_daily_quota_error(genai_errors.ClientError(429, {"error": {"details": [{"violations": [{"quotaId": DAILY}]}]}}))
    assert not is_daily_quota_error(_quota_error(MINUTE))
    assert not is_daily_quota_error(_quota_error(MINUTE, "Generate content requests per day limit exceeded"))
    assert is_daily_quota_error(ModelThrottledException("Generate content requests per day limit exceeded on Free Tier"))
    assert not is_daily_quota_error(ModelThrottledException("Please retry in 10s"))
    assert not is_daily_quota_error(ValueError("x"))


def _fallback_model(monkeypatch, errors, fallbacks, throttle_waits=0, overload_waits=0):
    """Model whose underlying stream raises the next of `errors` per call (None = succeed); records the model id used.

    throttle_waits defaults to 0 so the per-minute tests below exercise the switch path; the wait tests pass it."""
    used = []
    pending = list(errors)

    async def fake(self, *a, **k):
        used.append(self.config["model_id"])
        exc = pending.pop(0)
        if exc is not None:
            raise exc
        yield {"ok": True}

    monkeypatch.setattr(GeminiModel, "stream", fake)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a", fallback_model_ids=fallbacks,
                               throttle_waits=throttle_waits, overload_waits=overload_waits)
    slept: list[float] = []

    async def no_sleep(delay):
        slept.append(delay)

    model.sleep = no_sleep
    model.slept = slept
    return model, used


def test_daily_quota_switches_model(monkeypatch, capsys):
    model, used = _fallback_model(monkeypatch, [_quota_error(DAILY), None], ["b"])
    switches = []
    model.on_switch = lambda old, new: switches.append((old, new))
    assert _drain(model) == [{"ok": True}]
    assert used == ["a", "b"] and model.model_ids_used == ["a", "b"] and switches == [("a", "b")]
    assert "[model] a daily quota exhausted; switching to b" in capsys.readouterr().err


def test_daily_quota_walks_the_list_then_raises(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_quota_error(DAILY)] * 3, ["b", "c"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert used == ["a", "b", "c"] and model.model_ids_used == ["a", "b", "c"]


def test_per_minute_429_does_not_switch(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_quota_error(MINUTE)], ["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert used == ["a"] and model.model_ids_used == ["a"]


def test_no_switch_after_events_yielded(monkeypatch):
    async def partial(self, *a, **k):
        yield {"ok": True}
        raise _quota_error(DAILY)

    monkeypatch.setattr(GeminiModel, "stream", partial)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a", fallback_model_ids=["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert model.model_ids_used == ["a"]


def test_daily_quota_after_message_start_still_switches(monkeypatch):
    """Real order: strands yields messageStart, then the 429 surfaces when the stream is read."""
    used = []

    async def real_order(self, *a, **k):
        used.append(self.config["model_id"])
        yield {"messageStart": {"role": "assistant"}}
        if len(used) == 1:
            raise _quota_error(DAILY)
        yield {"ok": True}

    monkeypatch.setattr(GeminiModel, "stream", real_order)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a", fallback_model_ids=["b"])
    assert _drain(model) == [{"messageStart": {"role": "assistant"}}, {"ok": True}]
    assert used == ["a", "b"] and model.model_ids_used == ["a", "b"]


def test_message_start_alone_is_still_yielded(monkeypatch):
    async def only_start(self, *a, **k):
        yield {"messageStart": {"role": "assistant"}}

    monkeypatch.setattr(GeminiModel, "stream", only_start)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a")
    assert _drain(model) == [{"messageStart": {"role": "assistant"}}]


def test_switch_counts_as_model_call_not_throttle():
    from factory.guards import GuardLog, QuotaGuard

    q = QuotaGuard(5, GuardLog())
    q.extra_call()
    assert (q.calls, q.throttles) == (1, 0)


def _read_error() -> httpx.ReadError:
    return httpx.ReadError("connection reset")


def test_two_throttles_switch_and_resend(monkeypatch, capsys):
    model, used = _fallback_model(monkeypatch, [_quota_error(MINUTE), _quota_error(MINUTE), None], ["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert model.unresponsive_calls == 1 and model.model_switches == []
    assert _drain(model) == [{"ok": True}]
    assert used == ["a", "a", "b"] and model.model_ids_used == ["a", "b"]
    assert model.model_switches == [{"from": "a", "to": "b", "reason": "unresponsive:throttled"}]
    assert model.unresponsive_calls == 0
    assert "[model] a unresponsive (throttled); switching to b" in capsys.readouterr().err


def test_single_throttle_raises_without_switching(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_quota_error(MINUTE)], ["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert used == ["a"] and model.unresponsive_calls == 1 and model.model_switches == []


def test_content_event_resets_counter(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_quota_error(MINUTE), None, _quota_error(MINUTE)], ["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert _drain(model) == [{"ok": True}] and model.unresponsive_calls == 0
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert used == ["a", "a", "a"] and model.model_switches == [] and model.unresponsive_calls == 1


def test_transport_error_twice_switches(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_read_error(), _read_error(), None], ["b"])
    with pytest.raises(ModelThrottledException, match="ReadError"):
        _drain(model)
    assert _drain(model) == [{"ok": True}]
    assert used == ["a", "a", "b"]
    assert model.model_switches == [{"from": "a", "to": "b", "reason": "unresponsive:transport_error"}]


def test_503_twice_switches(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_server_error(503, "UNAVAILABLE")] * 2 + [None], ["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert _drain(model) == [{"ok": True}]
    assert model.model_switches[0]["reason"] == "unresponsive:server_error"


def test_stall_times_out_then_switches(monkeypatch):
    used = []

    async def stalls(self, *a, **k):
        used.append(self.config["model_id"])
        yield {"messageStart": {"role": "assistant"}}
        if len(used) <= 2:
            await asyncio.sleep(10)
        yield {"ok": True}

    monkeypatch.setattr(GeminiModel, "stream", stalls)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a", fallback_model_ids=["b"],
                               first_event_timeout_s=0.05)
    with pytest.raises(ModelThrottledException, match="no response within"):
        _drain(model)
    assert model.unresponsive_calls == 1
    assert _drain(model) == [{"messageStart": {"role": "assistant"}}, {"ok": True}]
    assert used == ["a", "a", "b"]
    assert model.model_switches == [{"from": "a", "to": "b", "reason": "unresponsive:stall"}]


def test_no_fallbacks_left_raises_after_threshold(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_quota_error(MINUTE)] * 3, [])
    for _ in range(3):
        with pytest.raises(ModelThrottledException):
            _drain(model)
    assert used == ["a"] * 3 and model.model_switches == [] and model.unresponsive_calls == 3


def test_daily_quota_records_switch_reason(monkeypatch):
    model, _ = _fallback_model(monkeypatch, [_quota_error(DAILY), None], ["b"])
    assert _drain(model) == [{"ok": True}]
    assert model.model_switches == [{"from": "a", "to": "b", "reason": "daily_quota"}]


def test_unresponsive_reason():
    assert unresponsive_reason(_quota_error(MINUTE)) == "throttled"
    assert unresponsive_reason(_quota_error(DAILY)) is None
    assert unresponsive_reason(_server_error(503, "UNAVAILABLE")) == "server_error"
    assert unresponsive_reason(_read_error()) == "transport_error"
    assert unresponsive_reason(ValueError("x")) is None


def test_model_switch_serialises_with_from_key():
    from factory.schemas import RunResult

    r = RunResult(run_id="r1", agent="builder", phase="plan", success=True, stop_reason="end_turn",
                  model_switches=[{"from": "a", "to": "b", "reason": "daily_quota"}])
    assert json.loads(r.model_dump_json())["model_switches"] == [{"from": "a", "to": "b", "reason": "daily_quota"}]


class _FakeSpan:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def add_event(self, name, attributes=None):
        self.events.append((name, dict(attributes or {})))

    def set_attribute(self, key, value):
        pass


@pytest.fixture
def span(monkeypatch):
    fake = _FakeSpan()
    monkeypatch.setattr("factory.gemini.trace.get_current_span", lambda: fake)
    return fake


def _retry(model_id, reason, attempt):
    return ("factory.model_retry", {"factory.model_id": model_id, "factory.retry.reason": reason,
                                    "factory.retry.attempt": attempt})


def test_two_throttles_emit_retry_events_then_switch(monkeypatch, span):
    model, _ = _fallback_model(monkeypatch, [_quota_error(MINUTE), _quota_error(MINUTE), None], ["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert _drain(model) == [{"ok": True}]
    assert [name for name, _ in span.events] == ["factory.model_retry", "factory.model_retry", "factory.model_switch"]
    assert span.events[0] == _retry("a", "throttled", 1)
    assert span.events[1] == _retry("a", "throttled", 2)


def test_stall_emits_retry_event(monkeypatch, span):
    async def stalls(self, *a, **k):
        yield {"messageStart": {"role": "assistant"}}
        await asyncio.sleep(10)

    monkeypatch.setattr(GeminiModel, "stream", stalls)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a", first_event_timeout_s=0.05)
    with pytest.raises(ModelThrottledException, match="no response within"):
        _drain(model)
    assert span.events == [_retry("a", "stall", 1)]


def test_daily_quota_without_fallback_still_emits_retry_event(monkeypatch, span):
    model, _ = _fallback_model(monkeypatch, [_quota_error(DAILY)], [])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert span.events == [_retry("a", "daily_quota", 1)]


def test_failure_after_content_emits_no_retry_event(monkeypatch, span):
    async def partial(self, *a, **k):
        yield {"ok": True}
        raise _quota_error(MINUTE)

    monkeypatch.setattr(GeminiModel, "stream", partial)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a", fallback_model_ids=["b"])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    assert span.events == []


def test_throttle_delay():
    assert throttle_delay(_quota_error(MINUTE, retry_delay="23s"), 0, 65) == 24.0
    assert throttle_delay(_quota_error(MINUTE, retry_delay="120s"), 0, 65) == 65
    assert throttle_delay(ModelThrottledException("Please retry in 9.5s."), 0, 65) == 10.5
    assert [throttle_delay(_quota_error(MINUTE), n, 65) for n in range(4)] == [15.0, 30.0, 60.0, 65]


def test_per_minute_throttle_waits_on_the_same_model(monkeypatch, capsys, span):
    model, used = _fallback_model(monkeypatch, [_quota_error(MINUTE, retry_delay="20s")] * 2 + [None], ["b"],
                                  throttle_waits=3)
    waits = []
    model.on_throttle_wait = lambda: waits.append(1)
    assert _drain(model) == [{"ok": True}]
    assert used == ["a", "a", "a"] and model.model_switches == [] and model.slept == [21.0, 21.0]
    assert len(waits) == 2 and model.throttle_waits_done == 0 and model.unresponsive_calls == 0
    assert span.events == [_retry("a", "throttled_wait", 1), _retry("a", "throttled_wait", 2)]
    assert "[model] a throttled (per minute); waiting 21s" in capsys.readouterr().err


def test_throttle_waits_exhausted_then_switches(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_quota_error(MINUTE)] * 4 + [_quota_error(MINUTE), None], ["b"],
                                  throttle_waits=3)
    with pytest.raises(ModelThrottledException):
        _drain(model)  # three waits, then the fourth throttle counts as unresponsive and is raised to strands
    assert used == ["a"] * 4 and model.slept == [15.0, 30.0, 60.0] and model.unresponsive_calls == 1
    assert _drain(model) == [{"ok": True}]  # strands' retry: no waits left, second unresponsive switches
    assert used == ["a"] * 5 + ["b"]
    assert model.model_switches == [{"from": "a", "to": "b", "reason": "unresponsive:throttled"}]
    assert model.throttle_waits_done == 0


def test_daily_quota_does_not_wait(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_quota_error(DAILY, retry_delay="80000s"), None], ["b"],
                                  throttle_waits=3)
    assert _drain(model) == [{"ok": True}]
    assert used == ["a", "b"] and model.slept == []


def test_throttle_wait_counts_a_throttle_not_a_call():
    from factory.guards import GuardLog, QuotaGuard

    q = QuotaGuard(5, GuardLog())
    q.throttle_wait()
    assert (q.calls, q.throttles) == (0, 1)


def _payment_required() -> genai_errors.ClientError:
    return genai_errors.ClientError(402, {"error": {"code": 402, "message": "Your prepayment credits are depleted.",
                                                    "status": "PAYMENT_REQUIRED"}})


def _spend_cap() -> ModelThrottledException:
    return _quota_error(MINUTE, "Your project has exceeded its monthly spending cap.")


def test_is_billing_error():
    assert is_billing_error(_payment_required())
    assert is_billing_error(_spend_cap())
    assert is_billing_error(ModelThrottledException("429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted."))
    assert not is_billing_error(_quota_error(MINUTE))
    assert not is_billing_error(_quota_error(DAILY))
    assert not is_billing_error(_server_error(503, "UNAVAILABLE"))


def test_402_stops_without_wait_or_switch(monkeypatch, capsys):
    model, used = _fallback_model(monkeypatch, [_payment_required(), None], ["b"], throttle_waits=3)
    with pytest.raises(ModelBillingError, match="billing blocked"):
        _drain(model)
    assert used == ["a"] and model.slept == [] and model.model_switches == []
    assert "[model] billing blocked" in capsys.readouterr().err


def test_spend_cap_429_stops_without_wait_or_switch(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_spend_cap(), None], ["b"], throttle_waits=3)
    with pytest.raises(ModelBillingError):
        _drain(model)
    assert used == ["a"] and model.slept == [] and model.model_switches == []


def test_other_client_errors_pass_through(monkeypatch):
    model, _ = _fallback_model(monkeypatch, [genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad",
                                                                                      "status": "INVALID_ARGUMENT"}})], ["b"])
    with pytest.raises(genai_errors.ClientError):
        _drain(model)


# --- key tiers and the paid budget -------------------------------------------------------------------------

from factory.gemini import KeyTier, ModelOverloadedError, ModelPaidBudgetError, request_cost_usd  # noqa: E402

PRICES = {"a": {"input": 1.0, "output": 10.0}, "b": {"input": 2.0, "output": 20.0}}
FREE, PAID = KeyTier("free", "kf", False), KeyTier("paid", "kp", True)


def _usage_event(inp, out):
    return {"metadata": {"usage": {"inputTokens": inp, "outputTokens": out, "totalTokens": inp + out}}}


def _tier_model(monkeypatch, errors, tiers, allowance=1.0, fallbacks=("b",), usage=(1000, 100)):
    """Like _fallback_model; records (key, model) per request; a success yields content plus usage metadata."""
    used = []
    pending = list(errors)

    async def fake(self, *a, **k):
        used.append((self.client_args["api_key"], self.config["model_id"]))
        exc = pending.pop(0) if pending else None
        if exc is not None:
            raise exc
        yield {"ok": True}
        yield _usage_event(*usage)

    monkeypatch.setattr(GeminiModel, "stream", fake)
    model = FactoryGeminiModel(key_tiers=list(tiers), model_id="a", fallback_model_ids=list(fallbacks),
                               paid_allowance_usd=allowance, prices=PRICES, throttle_waits=0)
    return model, used


def test_request_cost_usd():
    assert request_cost_usd("a", {"inputTokens": 1_000_000, "outputTokens": 100_000}, PRICES) == 2.0
    assert request_cost_usd("zzz", {"inputTokens": 1_000_000, "outputTokens": 0}, PRICES) == 2.0  # dearest
    assert request_cost_usd("a", {}, {}) == 0.0


def test_free_chain_first_then_paid_primary(monkeypatch, capsys):
    model, used = _tier_model(monkeypatch, [_quota_error(DAILY), _quota_error(DAILY), None], [FREE, PAID])
    _drain(model)
    assert used == [("kf", "a"), ("kf", "b"), ("kp", "a")]
    assert model.tiers_used == ["free", "paid"]
    assert model.model_switches[-1] == {"from": "b", "to": "a", "reason": "paid_key:daily_quota"}
    assert "free key exhausted (daily_quota); switching to the paid key" in capsys.readouterr().err
    assert model.paid_cost_usd == request_cost_usd("a", {"inputTokens": 1000, "outputTokens": 100}, PRICES)


def test_free_key_usage_costs_nothing(monkeypatch):
    model, _ = _tier_model(monkeypatch, [None], [FREE, PAID])
    _drain(model)
    assert model.paid_cost_usd == 0 and model.tiers_used == ["free"]


def test_paid_allowance_spent_stops_before_the_next_paid_request(monkeypatch):
    model, used = _tier_model(monkeypatch, [None, None], [PAID], allowance=0.002, usage=(1000, 100))
    _drain(model)  # costs 0.001 + 0.001 = 0.002
    with pytest.raises(ModelPaidBudgetError, match="paid allowance spent"):
        _drain(model)
    assert used == [("kp", "a")]


def test_zero_allowance_with_only_a_paid_key_makes_no_request(monkeypatch):
    model, used = _tier_model(monkeypatch, [None], [PAID], allowance=0)
    with pytest.raises(ModelPaidBudgetError):
        _drain(model)
    assert used == []


def test_free_exhausted_and_paid_spent_is_a_budget_stop(monkeypatch):
    model, used = _tier_model(monkeypatch, [_quota_error(DAILY), _quota_error(DAILY)], [FREE, PAID], allowance=0)
    with pytest.raises(ModelPaidBudgetError, match="free models exhausted"):
        _drain(model)
    assert used == [("kf", "a"), ("kf", "b")]


def test_single_default_key_never_counts_cost(monkeypatch):
    model, _ = _tier_model(monkeypatch, [None], [KeyTier("default", "k", False)], allowance=0)
    _drain(model)
    assert model.paid_cost_usd == 0


def test_unresponsive_free_chain_stops_instead_of_paying(monkeypatch, capsys):
    """A busy free tier is not a reason to pay: transient failures never move a run onto the paid key."""
    errs = [_quota_error(MINUTE)] * 4  # two per model on the free key
    model, used = _tier_model(monkeypatch, errs, [FREE, PAID])
    for _ in range(2):  # a switch resends at once, so only the first throttle on each model is raised
        with pytest.raises(ModelThrottledException):
            _drain(model)
    with pytest.raises(ModelOverloadedError, match="paid key is kept"):
        _drain(model)
    assert used == [("kf", "a"), ("kf", "a"), ("kf", "b"), ("kf", "b")]
    assert model.tiers_used == ["free"] and model.paid_cost_usd == 0
    assert "not using the paid key for a transient failure" in capsys.readouterr().err


def test_daily_quota_after_transient_switch_still_reaches_paid(monkeypatch):
    errs = [_quota_error(MINUTE), _quota_error(MINUTE), _quota_error(DAILY), None]
    model, used = _tier_model(monkeypatch, errs, [FREE, PAID])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    _drain(model)
    assert used == [("kf", "a"), ("kf", "a"), ("kf", "b"), ("kp", "a")]
    assert model.model_switches[-1]["reason"] == "paid_key:daily_quota"


def test_transient_failures_on_the_paid_tier_walk_the_paid_chain(monkeypatch):
    errs = [_quota_error(DAILY), _quota_error(DAILY), _quota_error(MINUTE), _quota_error(MINUTE), None]
    model, used = _tier_model(monkeypatch, errs, [FREE, PAID])
    with pytest.raises(ModelThrottledException):
        _drain(model)
    _drain(model)
    assert used == [("kf", "a"), ("kf", "b"), ("kp", "a"), ("kp", "a"), ("kp", "b")]


def test_single_key_transient_exhaustion_raises_as_before(monkeypatch):
    model, _ = _fallback_model(monkeypatch, [_quota_error(MINUTE)] * 2, [])
    for _ in range(2):
        with pytest.raises(ModelThrottledException):
            _drain(model)


def test_503_is_waited_out_on_the_same_model(monkeypatch, capsys):
    model, used = _fallback_model(monkeypatch, [_server_error(503, "UNAVAILABLE")] * 2 + [None], ["b"],
                                  overload_waits=3)
    slept, waits = [], []

    async def fake_sleep(s):
        slept.append(s)

    model.sleep = fake_sleep
    model.on_throttle_wait = lambda: waits.append(1)
    assert _drain(model) == [{"ok": True}]
    assert used == ["a", "a", "a"] and model.model_switches == []
    assert slept == [20, 40] and len(waits) == 2
    assert model.overload_waits_done == 0  # reset by the content event
    assert "a overloaded (503); waiting 20s" in capsys.readouterr().err


def test_503_waits_are_capped_then_switch(monkeypatch):
    model, used = _fallback_model(monkeypatch, [_server_error(503, "UNAVAILABLE")] * 5 + [None], ["b"],
                                  overload_waits=3)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    model.sleep = fake_sleep
    with pytest.raises(ModelThrottledException):  # waits used up: first strike, raised for strands to retry
        _drain(model)
    assert _drain(model) == [{"ok": True}]  # second strike switches to b and resends
    assert slept == [20, 40, 60]
    assert used == ["a", "a", "a", "a", "a", "b"]
    assert model.model_switches == [{"from": "a", "to": "b", "reason": "unresponsive:server_error"}]


def _paid_chain_model(monkeypatch, errors, tiers):
    used = []
    pending = list(errors)

    async def fake(self, *a, **k):
        used.append((self.client_args["api_key"], self.config["model_id"]))
        exc = pending.pop(0) if pending else None
        if exc is not None:
            raise exc
        yield {"ok": True}

    monkeypatch.setattr(GeminiModel, "stream", fake)
    model = FactoryGeminiModel(key_tiers=list(tiers), model_id="smart", fallback_model_ids=["smart2"],
                               paid_model_ids=["cheap", "cheap2"], paid_allowance_usd=1.0, prices=PRICES,
                               throttle_waits=0)
    return model, used


def test_paid_tier_walks_its_own_cheaper_chain(monkeypatch):
    model, used = _paid_chain_model(monkeypatch, [_quota_error(DAILY), _quota_error(DAILY), _quota_error(DAILY), None],
                                    [FREE, PAID])
    _drain(model)
    assert used == [("kf", "smart"), ("kf", "smart2"), ("kp", "cheap"), ("kp", "cheap2")]
    assert model.model_ids_used == ["smart", "smart2", "cheap", "cheap2"]


def test_paid_key_alone_starts_on_the_paid_chain(monkeypatch):
    model, used = _paid_chain_model(monkeypatch, [None], [PAID])
    _drain(model)
    assert used == [("kp", "cheap")] and model.model_ids_used == ["cheap"] and model.fallback_model_ids == ["cheap2"]
