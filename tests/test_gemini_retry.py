import asyncio

import pytest
from google.genai import errors as genai_errors
from strands.models.gemini import GeminiModel
from strands.types.exceptions import ModelThrottledException

from factory.gemini import FactoryGeminiModel, is_daily_quota_error, is_retryable_server_error


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
