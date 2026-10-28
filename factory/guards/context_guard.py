"""ContextGuard: truncate oversized tool results, and build the context_manager config."""

from __future__ import annotations

from typing import Any

from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

from .events import GuardLog


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    omitted = len(text) - max_chars
    return f"{text[:head]}\n[... {omitted} chars truncated by context_guard ...]\n{text[-tail:]}"


def truncate_tool_result(result: Any, max_chars: int) -> tuple[Any, bool]:
    if not isinstance(result, dict) or "content" not in result:
        return result, False
    changed = False
    content = []
    for block in result["content"]:
        if isinstance(block, dict) and isinstance(block.get("text"), str) and len(block["text"]) > max_chars:
            block = {**block, "text": truncate_text(block["text"], max_chars)}
            changed = True
        content.append(block)
    return ({**result, "content": content} if changed else result), changed


class ContextGuard(HookProvider):
    def __init__(self, max_chars: int, log: GuardLog) -> None:
        self.max_chars = max_chars
        self.log = log

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:  # noqa: ANN003
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        new, changed = truncate_tool_result(event.result, self.max_chars)
        if changed:
            event.result = new
            self.log.record("context_guard", "tool_result_truncated", event.tool_use.get("name", ""))


def build_context_kwargs(ctx: dict[str, Any]) -> dict[str, Any]:
    """Agent kwargs for context management: Offload strategies when available, else sliding window."""
    try:
        from strands.experimental.context_manager import ContextManager, Offload

        strategies = [
            Offload.truncate("tool_results").when(threshold=int(ctx.get("offload_threshold_tokens", 2000))),
            Offload.summarize("*").when(
                utilization=float(ctx.get("summarize_utilization", 0.8)),
                preserve_recent=int(ctx.get("preserve_recent", 4)),
            ),
        ]
        return {"context_manager": ContextManager(strategies=strategies)}
    except ImportError:  # pragma: no cover - older strands
        from strands.agent.conversation_manager import SlidingWindowConversationManager

        return {"conversation_manager": SlidingWindowConversationManager()}
