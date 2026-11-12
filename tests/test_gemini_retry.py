import asyncio
import json

import httpx
import pytest
from google.genai import errors as genai_errors
from strands.models.gemini import GeminiModel
from strands.types.exceptions import ModelThrottledException

from factory.gemini import FactoryGeminiModel, is_daily_quota_error, is_retryable_server_error, unresponsive_reason


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
    return FactoryGeminiModel(client_args={"api_key": "x"}, model_id="m")


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


def _quota_error(quota_id: str, message: str = "Quota exceeded") -> ModelThrottledException:
    details = {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]}]}}
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


def _fallback_model(monkeypatch, errors, fallbacks):
    """Model whose underlying stream raises the next of `errors` per call (None = succeed); records the model id used."""
    used = []
    pending = list(errors)

    async def fake(self, *a, **k):
        used.append(self.config["model_id"])
        exc = pending.pop(0)
        if exc is not None:
            raise exc
        yield {"ok": True}

    monkeypatch.setattr(GeminiModel, "stream", fake)
    model = FactoryGeminiModel(client_args={"api_key": "x"}, model_id="a", fallback_model_ids=fallbacks)
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
