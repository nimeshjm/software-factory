"""EditGuard: tell an implement or revise run to start editing when it has spent its budget reading.

The PR 137 revise (2026-11-24) read and searched 22 times, made no edit, and reached the budget guard's
85% nudge with nothing to commit. This guard fires earlier and does not end the turn: it appends a short
instruction to the tool result the model is about to read, at most twice per run, and only while no
`edit_file` has succeeded yet.
"""

from __future__ import annotations

from typing import Any

from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

from .events import GuardLog

READ_TOOLS = frozenset({"read_file", "search_files", "list_files"})
EDIT_PHASES = frozenset({"implement", "revise"})
GOAL = {"implement": "the plan's steps", "revise": "what the review asks for"}

NUDGE = ("[edit_guard] {why} and no file has been edited yet. Stop exploring: make the edits for {goal} now "
         "with edit_file, then run_checks. Read only what a specific edit needs, as a narrow search_files or a "
         "read_file line range. If the work does not fit the remaining budget, make the smallest change that "
         "fixes the failing check and list the rest in notes.")


class EditGuard(HookProvider):
    def __init__(self, phase: str, limits: dict[str, int], log: GuardLog, token_fraction: float = 0.35,
                 read_limit: int = 12) -> None:
        self.phase = phase
        self.log = log
        self.enabled = phase in EDIT_PHASES
        self.tokens_cap = int(token_fraction * limits["total_tokens"]) if limits.get("total_tokens") else None
        self.read_limit = read_limit
        self.reads = 0
        self.edited = False
        self.nudges = 0  # at most two: at the first threshold, and again at twice the read limit

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        if not self.enabled or self.edited:
            return
        try:
            name = event.tool_use.get("name", "")
            if name == "edit_file":
                if isinstance(event.result, dict) and event.result.get("status") == "success":
                    self.edited = True
                return
            if name not in READ_TOOLS:
                return
            self.reads += 1
            why = self._why(event)
            if why is None:
                return
            self.nudges += 1
            text = NUDGE.format(why=why, goal=GOAL[self.phase])
            result = event.result if isinstance(event.result, dict) else {}
            event.result = {**result, "content": [*(result.get("content") or []), {"text": text}]}
            self.log.record("edit_guard", "nudge", why)
        except Exception:  # noqa: BLE001 - a hook must never break the run
            return

    def _why(self, event: Any) -> str | None:
        if self.nudges >= 2:
            return None
        if self.nudges == 1:
            return f"{self.reads} reads" if self.reads >= 2 * self.read_limit else None
        if self.reads >= self.read_limit:
            return f"{self.reads} reads"
        tokens = _tokens(event)
        if self.tokens_cap is not None and tokens >= self.tokens_cap:
            return f"{tokens} of the phase's tokens used"
        return None


def _tokens(event: Any) -> int:
    agent = getattr(event, "agent", None)
    inv = getattr(getattr(agent, "event_loop_metrics", None), "latest_agent_invocation", None)
    usage = getattr(inv, "usage", None) or {}
    return int(usage.get("totalTokens", 0) or 0)
