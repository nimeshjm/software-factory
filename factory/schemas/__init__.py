"""Proposal schemas and helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .target_profile import TargetProfile
from .models import (
    AgentCheck,
    BuildProposal,
    CiFix,
    GuardEvent,
    Incident,
    IncidentProposal,
    InlineComment,
    PlanArtifact,
    ResearchArtifact,
    ReviewProposal,
    RunResult,
    ToolCall,
    TriageProposal,
    Usage,
    VerifyBug,
    VerifyProposal,
)

_PHASE_MODELS: dict[tuple[str, str | None], type[BaseModel]] = {
    ("triage", None): TriageProposal,
    ("builder", "research"): ResearchArtifact,
    ("builder", "plan"): PlanArtifact,
    ("builder", "implement"): BuildProposal,
    ("bug-fixer", "research"): ResearchArtifact,
    ("bug-fixer", "plan"): PlanArtifact,
    ("bug-fixer", "implement"): BuildProposal,
    ("reviewer", None): ReviewProposal,
    ("verifier", None): VerifyProposal,
    ("incident-responder", None): IncidentProposal,
}

ALL_MODELS: dict[str, type[BaseModel]] = {
    m.__name__: m
    for m in (
        TriageProposal,
        ResearchArtifact,
        PlanArtifact,
        BuildProposal,
        ReviewProposal,
        VerifyProposal,
        IncidentProposal,
        RunResult,
        TargetProfile,
    )
}


def proposal_model_for(agent: str, phase: str) -> type[BaseModel]:
    if (agent, phase) in _PHASE_MODELS:
        return _PHASE_MODELS[(agent, phase)]
    if (agent, None) in _PHASE_MODELS:
        return _PHASE_MODELS[(agent, None)]
    raise KeyError(f"no proposal model for agent={agent!r} phase={phase!r}")


def dump_schemas(out_dir: str | Path | None = None) -> dict[str, Any]:
    """Write JSON schemas to ``out_dir`` (one file per model) or stdout when None."""
    schemas = {name: m.model_json_schema() for name, m in ALL_MODELS.items()}
    if out_dir is None:
        json.dump(schemas, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        for name, schema in schemas.items():
            (d / f"{name}.schema.json").write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    return schemas


def _validate_profile(args: list[str]) -> int:
    import argparse

    import yaml
    from pydantic import ValidationError

    ap = argparse.ArgumentParser(prog="python -m factory.schemas validate-profile")
    ap.add_argument("--file", required=True)
    ap.add_argument("--write", action="store_true", help="rewrite FILE as normalised JSON")
    ns = ap.parse_args(args)
    try:
        with open(ns.file, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)  # YAML is a superset of JSON
    except (OSError, yaml.YAMLError) as exc:
        sys.stderr.write(f"invalid profile {ns.file}: cannot read: {exc}\n")
        return 1
    if not isinstance(raw, dict):
        sys.stderr.write(f"invalid profile {ns.file}: file is empty or not a mapping\n")
        return 1
    try:
        profile = TargetProfile.model_validate(raw)
    except ValidationError as exc:
        lines = [f"  {'.'.join(str(x) for x in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()]
        sys.stderr.write(f"invalid profile {ns.file}:\n" + "\n".join(lines) + "\n")
        return 1
    if ns.write:
        with open(ns.file, "w", encoding="utf-8") as fh:
            fh.write(profile.model_dump_json(indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI. ``dump [out_dir]`` writes schemas; ``validate --agent A --phase P --file F`` checks a proposal."""
    import argparse

    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "validate":
        ap = argparse.ArgumentParser(prog="python -m factory.schemas validate")
        ap.add_argument("--agent", required=True)
        ap.add_argument("--phase", required=True)
        ap.add_argument("--file", required=True)
        ns = ap.parse_args(args[1:])
        model = proposal_model_for(ns.agent, ns.phase)
        try:
            with open(ns.file, encoding="utf-8") as fh:
                model.model_validate(json.load(fh))
        except Exception as exc:  # noqa: BLE001 - report any failure as invalid
            sys.stderr.write(f"invalid {model.__name__}: {exc}\n")
            return 1
        return 0
    if args and args[0] == "validate-profile":
        return _validate_profile(args[1:])
    if args and args[0] == "dump":
        args = args[1:]
    dump_schemas(args[0] if args else None)
    return 0


__all__ = [
    "ALL_MODELS", "AgentCheck", "BuildProposal", "CiFix", "GuardEvent", "ToolCall", "Incident", "IncidentProposal",
    "InlineComment", "PlanArtifact", "ResearchArtifact", "ReviewProposal", "RunResult", "TargetProfile", "TriageProposal",
    "Usage", "VerifyBug", "VerifyProposal", "dump_schemas", "proposal_model_for",
]
