"""GeminiModel that also retries 503 UNAVAILABLE ("model is experiencing high demand").

Strands maps RESOURCE_EXHAUSTED/UNAVAILABLE to ModelThrottledException only for google-genai ClientError (4xx).
A 503 arrives as ServerError, so the run died on the first overloaded call instead of backing off.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.genai import errors as genai_errors
from strands.models.gemini import GeminiModel
from strands.types.exceptions import ModelThrottledException

RETRYABLE_SERVER_CODES = frozenset({503})


def is_retryable_server_error(error: BaseException) -> bool:
    return isinstance(error, genai_errors.ServerError) and (
        error.code in RETRYABLE_SERVER_CODES or error.status == "UNAVAILABLE")


class FactoryGeminiModel(GeminiModel):
    async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        try:
            async for event in super().stream(*args, **kwargs):
                yield event
        except genai_errors.ServerError as error:
            if is_retryable_server_error(error):
                raise ModelThrottledException(error.message or str(error)) from error
            raise
