"""OTLP setup (no-op without OTEL_EXPORTER_OTLP_ENDPOINT) and flush."""

from __future__ import annotations

import os

_telemetry = None

# Latest OTel GenAI conventions, which Honeycomb's Agent Timeline reads: gen_ai.provider.name,
# gen_ai.input.messages / gen_ai.output.messages (as span events, so one oversized prompt cannot
# drop the chat span itself) and gen_ai.tool.call.arguments / .result on execute_tool spans.
SEMCONV_OPT_IN = "gen_ai_latest_experimental"


def agent_name(agent: str, phase: str) -> str:
    """gen_ai.agent.name: one name per agent and phase, so the timeline groups phases apart."""
    return f"{agent}/{phase}" if phase and phase != agent else agent


def setup(run_id: str = "", agent: str = "", phase: str = "", issue: int | None = None) -> bool:
    """Configure OTLP export. Returns True when an exporter was set up."""
    global _telemetry
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    os.environ.setdefault("OTEL_SERVICE_NAME", "software-factory")
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", SEMCONV_OPT_IN)
    extra = {"factory.run_id": run_id, "factory.agent": agent, "factory.phase": phase}
    if issue is not None:
        extra["work_tracker.issue_id"] = str(issue)
    existing = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    have = {kv.split("=", 1)[0] for kv in existing.split(",") if "=" in kv}
    add = ",".join(f"{k}={v}" for k, v in extra.items() if v and k not in have)
    os.environ["OTEL_RESOURCE_ATTRIBUTES"] = ",".join(x for x in (existing, add) if x)
    from strands.telemetry import StrandsTelemetry

    _telemetry = StrandsTelemetry().setup_otlp_exporter()
    return True


def flush() -> None:
    global _telemetry
    if _telemetry is None:
        return
    try:
        _telemetry.tracer_provider.force_flush()
        _telemetry.tracer_provider.shutdown()
    except Exception:  # noqa: BLE001
        pass
    _telemetry = None
