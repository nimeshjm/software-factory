"""OTLP setup (no-op without OTEL_EXPORTER_OTLP_ENDPOINT) and flush."""

from __future__ import annotations

import os

_telemetry = None


def setup(run_id: str = "", agent: str = "", phase: str = "", issue: int | None = None) -> bool:
    """Configure OTLP export. Returns True when an exporter was set up."""
    global _telemetry
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    os.environ.setdefault("OTEL_SERVICE_NAME", "software-factory")
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
