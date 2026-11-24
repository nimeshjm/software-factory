"""PromptStats: what each model request's input is made of, as Honeycomb span events.

Per-call input was about 23k tokens on 2026-11-24 and only the total was visible (`gen_ai.usage.input_tokens`).
Before every model request this hook sizes the parts strands will send and adds a `factory.prompt` span event:
system prompt, tool definitions, the task message, and the history after it split into tool results, the model's
own text and tool-call arguments (`edit_file` arguments separately: an edit's full text is resent on every later
call). Once per phase it also adds one `factory.task_section` event per `#`/`##` section of the system prompt
(`factory.task.part` `system`) and of the task message (`task`), so the fixed part can be cut where it is largest. Sizes are characters; `*_tokens` is chars / 4 rounded up, an estimate
(the real total per call is the chat span's `gen_ai.usage.input_tokens`).
"""

from __future__ import annotations

import json
import re
from typing import Any

from strands.hooks import BeforeModelCallEvent, HookProvider, HookRegistry

SECTION_RE = re.compile(r"(?m)^#{1,2} (.+)$")
MAX_SECTIONS = 40


def _tok(chars: int) -> int:
    return (chars + 3) // 4


def _json_len(obj: Any) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return len(str(obj))


def _block_text_len(block: dict[str, Any]) -> int:
    if "text" in block and isinstance(block["text"], str):
        return len(block["text"])
    if "json" in block:
        return _json_len(block["json"])
    return _json_len(block)


def message_parts(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Character counts by part: task (first user message), tool_results, assistant_text, tool_args, edit_args, other."""
    parts = {"task": 0, "tool_results": 0, "assistant_text": 0, "tool_args": 0, "edit_args": 0, "other": 0}
    for i, msg in enumerate(messages):
        role = msg.get("role")
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if i == 0 and role == "user" and "text" in block:
                parts["task"] += len(block.get("text") or "")
            elif "toolResult" in block:
                parts["tool_results"] += sum(_block_text_len(c) for c in block["toolResult"].get("content") or []
                                             if isinstance(c, dict))
            elif "toolUse" in block:
                use = block["toolUse"]
                n = _json_len(use.get("input"))
                parts["edit_args" if use.get("name") == "edit_file" else "tool_args"] += n
            elif role == "assistant" and ("text" in block or "reasoningContent" in block):
                parts["assistant_text"] += _block_text_len(block) if "text" in block else _json_len(block)
            else:
                parts["other"] += _block_text_len(block)
    return parts


def task_sections(text: str) -> list[tuple[str, int]]:
    """(heading, chars) per `#`/`##` section of the task message; text before the first heading is `(preamble)`.

    Repeated headings are numbered (`Goal`, `Goal #2`) so a duplicated block shows up twice."""
    out: list[tuple[str, int]] = []
    matches = list(SECTION_RE.finditer(text))
    first = matches[0].start() if matches else len(text)
    if first:
        out.append(("(preamble)", first))
    seen: dict[str, int] = {}
    for j, m in enumerate(matches):
        end = matches[j + 1].start() if j + 1 < len(matches) else len(text)
        head = re.sub(r"\s*\(.*$", "", m.group(1)).strip()[:60] or "(untitled)"
        seen[head] = seen.get(head, 0) + 1
        out.append((head if seen[head] == 1 else f"{head} #{seen[head]}", end - m.start()))
    if len(out) > MAX_SECTIONS:
        rest = sum(n for _, n in out[MAX_SECTIONS - 1:])
        out = out[:MAX_SECTIONS - 1] + [("(other sections)", rest)]
    return out


class PromptStats(HookProvider):
    def __init__(self) -> None:
        self.requests = 0
        self.sections_sent = False
        self.last: dict[str, int] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before_model)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        try:
            self._record(event)
        except Exception:  # noqa: BLE001 - telemetry must never break a run
            return

    def _record(self, event: BeforeModelCallEvent) -> None:
        from opentelemetry import trace

        agent = event.agent
        self.requests += 1
        messages = list(getattr(agent, "messages", None) or [])
        system = getattr(agent, "system_prompt", None) or ""
        try:
            specs = agent.tool_registry.get_all_tool_specs()
        except Exception:  # noqa: BLE001
            specs = []
        parts = message_parts(messages)
        sizes = {"system": len(system), "tools": _json_len(specs), **parts}
        total = sum(sizes.values())
        attrs: dict[str, Any] = {"factory.prompt.request": self.requests, "factory.prompt.messages": len(messages),
                                 "factory.prompt.tools": len(specs), "factory.prompt.total_chars": total,
                                 "factory.prompt.total_tokens": _tok(total),
                                 "factory.prompt.history_tokens": _tok(total - sizes["system"] - sizes["tools"]
                                                                       - sizes["task"])}
        for k, v in sizes.items():
            attrs[f"factory.prompt.{k}_tokens"] = _tok(v)
        projected = getattr(event, "projected_input_tokens", None)
        if projected:
            attrs["factory.prompt.projected_tokens"] = int(projected)
        self.last = {k: _tok(v) for k, v in sizes.items()}
        span = trace.get_current_span()
        span.add_event("factory.prompt", attrs)
        if not self.sections_sent and messages:
            self.sections_sent = True
            first = messages[0]
            text = "".join(b.get("text") or "" for b in first.get("content") or [] if isinstance(b, dict))
            for part, body in (("system", system), ("task", text)):
                for order, (head, chars) in enumerate(task_sections(body)):
                    span.add_event("factory.task_section", {
                        "factory.task.part": part, "factory.task.section": head, "factory.task.section_order": order,
                        "factory.task.section_chars": chars, "factory.task.section_tokens": _tok(chars)})
