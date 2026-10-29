"""Load factory.config.json and resolve an agent+phase into an effective RunSpec."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("FACTORY_ROOT") or Path(__file__).resolve().parent.parent)
DEFAULT_CONFIG_PATH = ROOT / "factory.config.json"

OVERRIDABLE = ("tools", "limits", "max_model_calls", "timeout_minutes", "model")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RunSpec:
    agent: str
    phase: str
    model_key: str
    model_id: str
    params: dict[str, Any]
    tools: list[str]
    limits: dict[str, int]
    max_model_calls: int
    timeout_minutes: float
    context: dict[str, Any] = field(default_factory=dict)
    fallback_model_ids: tuple[str, ...] = ()

    @property
    def timeout_seconds(self) -> float:
        return self.timeout_minutes * 60.0


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("FACTORY_CONFIG") or DEFAULT_CONFIG_PATH)
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_run_spec(cfg: dict[str, Any], agent: str, phase: str) -> RunSpec:
    agents = cfg.get("agents", {})
    if agent not in agents:
        raise ConfigError(f"unknown agent {agent!r}; known: {sorted(agents)}")
    a = agents[agent]
    phases = a.get("phases", [])
    if phase not in phases:
        raise ConfigError(f"agent {agent!r} has no phase {phase!r}; phases: {phases}")

    eff: dict[str, Any] = {k: a[k] for k in OVERRIDABLE if k in a}
    for k, v in (a.get("phase_overrides", {}).get(phase) or {}).items():
        if k not in OVERRIDABLE:
            raise ConfigError(f"phase_overrides[{phase}] has unsupported key {k!r}")
        eff[k] = v  # overrides replace the base value wholesale

    for required in ("model", "tools", "limits", "max_model_calls", "timeout_minutes"):
        if required not in eff:
            raise ConfigError(f"agent {agent!r} phase {phase!r} missing {required!r}")

    model_key = eff["model"]
    models = cfg.get("models", {})
    if model_key not in models:
        raise ConfigError(f"unknown model {model_key!r}")
    m = models[model_key]
    return RunSpec(
        agent=agent,
        phase=phase,
        model_key=model_key,
        model_id=m["model_id"],
        params=json.loads(json.dumps(m.get("params", {}))),
        tools=list(eff["tools"]),
        limits={k: int(v) for k, v in eff["limits"].items()},
        max_model_calls=int(eff["max_model_calls"]),
        timeout_minutes=float(eff["timeout_minutes"]),
        context=dict(cfg.get("context", {})),
        fallback_model_ids=tuple(m.get("fallback_model_ids", [])),
    )


SPIKE_MODEL_KEYS = {"research": "spike_research", "implement": "spike"}


def apply_spike_model(cfg: dict[str, Any], spec: RunSpec, labels: list[str]) -> RunSpec:
    """Builder research/implement on a `spike` issue use models.spike_research / models.spike instead of the phase's model (cap = min of the phase's and the model's max_model_calls)."""
    if spec.agent != "builder" or spec.phase not in SPIKE_MODEL_KEYS or "spike" not in labels:
        return spec
    key = SPIKE_MODEL_KEYS[spec.phase]
    m = cfg.get("models", {}).get(key)
    if not m:
        raise ConfigError(f"unknown model '{key}'")
    cap = min(spec.max_model_calls, int(m["max_model_calls"])) if "max_model_calls" in m else spec.max_model_calls
    return replace(spec, model_key=key, model_id=m["model_id"], params=json.loads(json.dumps(m.get("params", {}))),
                   fallback_model_ids=tuple(m.get("fallback_model_ids", [])), max_model_calls=cap)


def issue_labels(inputs_dir: str | Path) -> list[str]:
    """Label names from inputs/issue.json (strings or {name} objects); empty when absent."""
    p = Path(inputs_dir) / "issue.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("labels") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(x.get("name") if isinstance(x, dict) else x) for x in raw]


class ProfileError(ValueError):
    """The target profile (inputs/factory.json) is missing or invalid."""


def load_profile(inputs_dir: str | Path):
    """Load and validate the normalised target profile from ``<inputs_dir>/factory.json``."""
    return load_profile_file(Path(inputs_dir) / "factory.json")


def load_profile_file(p: str | Path):
    from pydantic import ValidationError

    from .schemas.target_profile import TargetProfile

    p = Path(p)
    if not p.is_file():
        raise ProfileError(f"target profile missing: {p} (factory.yml must exist on the target default branch)")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProfileError(f"target profile {p} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ProfileError(f"target profile {p} must be a JSON object")
    try:
        return TargetProfile.model_validate(raw)
    except ValidationError as e:
        detail = "; ".join(f"{'.'.join(str(x) for x in err['loc']) or '(root)'}: {err['msg']}" for err in e.errors())
        raise ProfileError(f"target profile {p} is invalid: {detail}") from e
