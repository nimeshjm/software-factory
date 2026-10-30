"""Stub Strands model for offline end-to-end tests (`--fake-model`)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from strands.models.model import Model

CANNED: dict[str, dict[str, Any]] = {
    "TriageProposal": {"issues": [], "new_issues": [], "summary": "fake triage"},
    "ResearchArtifact": {"research_md": "# Research\nfake"},
    "PlanArtifact": {"plan_md": "# Plan\nfake", "files": ["src/app.py"], "steps": ["do it"], "risk_notes": [],
                     "needs_human": False},
    "BuildProposal": {"branch": "feat/1-fake", "title": "Fake", "body": "Closes #1", "files": ["src/app.py"],
                      "checks_passed": True, "notes": ""},
    "ReviewProposal": {"verdict": "comment", "agent_checks": [], "questions_for_human": [],
                       "inline_comments": [], "summary_md": "fake", "ci_fix": {"attempted": False}},
    "VerifyProposal": {"outcome": "skipped", "evidence_md": "fake"},
    "IncidentProposal": {"incidents": [], "summary": "fake"},
    "SecurityProposal": {"findings": [], "resolved": [], "summary": "fake"},
}


class FakeModel(Model):
    """Replays a script. Each step is {"tool": name, "input": {...}} or {"text": "..."}.

    When the script is exhausted (or empty) it emits the structured-output tool with the canned proposal.
    """

    def __init__(self, script: list[dict[str, Any]] | None = None, canned: dict[str, Any] | None = None) -> None:
        self.script = list(script or [])
        self.canned = canned
        self.config: dict[str, Any] = {"model_id": "fake"}
        self.calls = 0

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> Any:
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # type: ignore[override]
        yield {"output": output_model(**(self.canned or CANNED[output_model.__name__]))}

    async def stream(self, messages, tool_specs=None, system_prompt=None, *, tool_choice=None,
                     structured_output_model=None, **kwargs) -> AsyncGenerator[dict, None]:  # type: ignore[override]
        self.calls += 1
        step = self.script.pop(0) if self.script else None
        so_name = structured_output_model.__name__ if structured_output_model else None
        if step is None:
            names = [t["toolSpec"]["name"] if "toolSpec" in t else t.get("name") for t in (tool_specs or [])]
            so_name = so_name or next((n for n in names if n in CANNED), None)
            step = ({"tool": so_name, "input": self.canned or CANNED[so_name]} if so_name
                    else {"text": "done"})
        yield {"messageStart": {"role": "assistant"}}
        if "tool" in step:
            yield {"contentBlockStart": {"start": {"toolUse": {"name": step["tool"],
                                                               "toolUseId": f"fake_{uuid.uuid4().hex[:8]}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(step["input"])}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                            "metrics": {"latencyMs": 1}}}
