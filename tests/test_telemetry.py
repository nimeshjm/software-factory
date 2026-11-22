"""Honeycomb agent conventions: conversation id, agent name, latest GenAI semconv."""

import json

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from factory import run as run_mod
from factory import telemetry

_EXPORTER = InMemorySpanExporter()


@pytest.fixture
def spans(monkeypatch):
    """Route Strands spans to memory, with the env telemetry.setup would set."""
    if not isinstance(trace_api.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace_api.set_tracer_provider(provider)
    _EXPORTER.clear()
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", telemetry.SEMCONV_OPT_IN)
    import strands.telemetry.tracer as st
    monkeypatch.setattr(st, "_tracer_instance", None)
    yield _EXPORTER
    monkeypatch.setattr(st, "_tracer_instance", None)


def test_agent_name():
    assert telemetry.agent_name("builder", "research") == "builder/research"
    assert telemetry.agent_name("reviewer", "review") == "reviewer/review"
    assert telemetry.agent_name("triage", "triage") == "triage"


def test_setup_sets_semconv_opt_in(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.invalid")
    monkeypatch.delenv("OTEL_SEMCONV_STABILITY_OPT_IN", raising=False)
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)

    class Fake:
        def setup_otlp_exporter(self):
            return self

    import strands.telemetry
    monkeypatch.setattr(strands.telemetry, "StrandsTelemetry", Fake)
    assert telemetry.setup("r1", "builder", "plan")
    import os
    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_latest_experimental"
    monkeypatch.setattr(telemetry, "_telemetry", None)


def test_run_spans_follow_honeycomb_agent_conventions(tmp_path, ws, monkeypatch, spans):
    from tests.test_e2e import _inputs
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps([{"tool": "list_files", "input": {"path": "."}}]))
    monkeypatch.setenv("FACTORY_FAKE_SCRIPT", str(sp))
    inputs = _inputs(tmp_path)
    run_id = json.loads((inputs / "run.json").read_text())["run_id"]
    run_mod.main(["--agent", "triage", "--phase", "triage", "--inputs", str(inputs),
                  "--out", str(tmp_path / "out"), "--fake-model"])
    got = spans.get_finished_spans()
    ops = {s.attributes.get("gen_ai.operation.name") for s in got}
    assert {"invoke_agent", "chat", "execute_tool"} <= ops
    for s in got:
        if s.attributes.get("gen_ai.operation.name"):
            assert s.attributes.get("gen_ai.conversation.id") == str(run_id), s.name
    agent_span = next(s for s in got if s.attributes.get("gen_ai.operation.name") == "invoke_agent")
    assert agent_span.name == "invoke_agent triage"
    assert agent_span.attributes["gen_ai.agent.name"] == "triage"
    assert agent_span.attributes["gen_ai.provider.name"] == "strands-agents"
    tool_span = next(s for s in got if s.name == "execute_tool list_files")
    assert "gen_ai.tool.call.arguments" in tool_span.attributes
