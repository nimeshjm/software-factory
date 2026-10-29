import asyncio

import pytest
from google.genai import errors as genai_errors
from strands.models.gemini import GeminiModel
from strands.types.exceptions import ModelThrottledException

from factory.gemini import FactoryGeminiModel, is_retryable_server_error


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
