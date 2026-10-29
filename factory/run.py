"""Harness entrypoint: python -m factory.run --agent A --phase P --inputs inputs/ --out out/ [--dry-run]."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from . import telemetry
from .config import ROOT, ProfileError, load_config, load_profile, resolve_run_spec
from .guards import ContextGuard, GuardLog, LatencyGuard, QuotaGuard, ToolCallLog, ToolGuard, build_context_kwargs
from .guards.output_guard import redact, scan_obj
from .tools.profile import set_profile
from .prompts import build_task_message, load_system_prompt
from .schemas import GuardEvent, RunResult, ToolCall, proposal_model_for

FINALIZE_PROMPT = "Time budget is spent. Emit the final structured proposal now, based on what you have."


def _build_model(spec, fake: bool):
    if fake:
        from .fake_model import FakeModel

        script_path = os.environ.get("FACTORY_FAKE_SCRIPT")
        script = json.loads(Path(script_path).read_text()) if script_path else None
        return FakeModel(script=script)
    from .gemini import FactoryGeminiModel

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return FactoryGeminiModel(client_args={"api_key": key}, model_id=spec.model_id, params=spec.params)


def _usage(res) -> dict[str, int]:
    u = getattr(getattr(res, "metrics", None), "accumulated_usage", None) or {}
    return {"input_tokens": int(u.get("inputTokens", 0)), "output_tokens": int(u.get("outputTokens", 0)),
            "total_tokens": int(u.get("totalTokens", 0))}


def _write(out: Path, name: str, text: str) -> None:
    (out / name).write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    inputs = Path(args.inputs)
    run_json: dict[str, Any] = {}
    if (inputs / "run.json").is_file():
        run_json = json.loads((inputs / "run.json").read_text())
    run_id = os.environ.get("FACTORY_RUN_ID") or run_json.get("run_id") or uuid.uuid4().hex[:12]
    log = GuardLog()
    quota: QuotaGuard | None = None
    latency: LatencyGuard | None = None
    tool_log: ToolCallLog | None = None
    result = RunResult(run_id=str(run_id), agent=args.agent, phase=args.phase, success=False,
                       stop_reason="not_started", dry_run=bool(args.dry_run))
    try:
        cfg = load_config()
        try:
            profile = load_profile(inputs)
        except ProfileError as e:
            result.stop_reason = "profile_invalid"
            result.error = redact(str(e))[:1000]
            return 1
        set_profile(profile)
        spec = resolve_run_spec(cfg, args.agent, args.phase)
        model_cls = proposal_model_for(args.agent, args.phase)
        telemetry.setup(str(run_id), args.agent, args.phase, run_json.get("issue_number"))

        from strands import Agent

        from .tools import build_tools

        quota = QuotaGuard(spec.max_model_calls, log)
        latency = LatencyGuard(spec.timeout_seconds, log)
        tool_log = ToolCallLog()
        hooks = [ToolGuard(log, profile), tool_log, quota, latency,
                 ContextGuard(int(spec.context.get("tool_result_max_chars", 20000)), log)]
        trace_attrs: dict[str, Any] = {"factory.run_id": str(run_id), "factory.agent": args.agent,
                                       "factory.phase": args.phase, "factory.dry_run": bool(args.dry_run)}
        if run_json.get("issue_number") is not None:
            trace_attrs["work_tracker.issue_id"] = str(run_json["issue_number"])
        agent = Agent(
            model=_build_model(spec, args.fake_model),
            system_prompt=load_system_prompt(args.agent, args.phase, Path(os.environ.get("FACTORY_ROOT") or ROOT), profile),
            tools=build_tools(spec.tools),
            hooks=hooks,
            callback_handler=None,
            load_tools_from_directory=False,
            trace_attributes=trace_attrs,
            **build_context_kwargs(spec.context),
        )
        task = build_task_message(inputs, args.agent, args.phase, [out], profile)
        res = agent(task, limits=spec.limits, structured_output_model=model_cls)
        if res.stop_reason == "end_turn" and res.structured_output is None and latency.nudged:
            res = agent(FINALIZE_PROMPT, limits={"turns": 3}, structured_output_model=model_cls)

        result.stop_reason = res.stop_reason
        result.cycles = int(res.metrics.cycle_count)
        usage = _usage(res)
        result.usage = type(result.usage)(**usage)
        so = res.structured_output
        if latency.hard_fired:
            result.stop_reason = "timeout"
            result.error = "latency_guard hard timeout"
        elif res.stop_reason not in ("end_turn", "tool_use"):
            result.error = f"stop_reason={res.stop_reason}"
        elif not isinstance(so, model_cls):
            result.error = "no valid structured proposal"
        else:
            clean, findings = scan_obj(so.model_dump(mode="json"), profile)
            if findings:
                result.error = f"output_guard blocked secret patterns: {', '.join(findings)}"
                log.record("output_guard", "secret_in_output", ", ".join(findings))
            else:
                result.success = True
                # Strands reports the model's raw stop reason ("tool_use") when the structured-output
                # tool ends the loop; normalise it so consumers see the contract value.
                result.stop_reason = "end_turn"
                _write(out, "proposal.json", json.dumps(clean, indent=2))
                if args.phase == "research":
                    _write(out, "research.md", clean["research_md"])
                elif args.phase == "plan":
                    _write(out, "plan.md", clean["plan_md"])
    except BaseException as e:  # noqa: BLE001 - result.json must always be written
        result.success = False
        if latency is not None and latency.hard_fired:
            result.stop_reason = "timeout"
        elif quota is not None and quota.capped:
            result.stop_reason = "quota_cap"
        else:
            result.stop_reason = "exception"
        result.error = redact(f"{type(e).__name__}: {e}")[:1000]
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        if latency is not None:
            latency.stop()
        result.model_calls = quota.calls if quota else 0
        result.throttles = quota.throttles if quota else 0
        result.duration_s = round(time.monotonic() - started, 2)
        result.guard_events = [GuardEvent(**e) for e in log.events]
        result.tool_calls = [ToolCall(**c) for c in tool_log.calls] if tool_log else []
        _write(out, "result.json", result.model_dump_json(indent=2))
        telemetry.flush()
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factory.run")
    p.add_argument("--agent", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--inputs", default="inputs/")
    p.add_argument("--out", default="out/")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--fake-model", action="store_true")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
