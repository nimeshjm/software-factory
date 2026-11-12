"""ContextGuard: oversized tool results are truncated, except for tools that clip themselves (fetch_url)."""
from types import SimpleNamespace

from factory.guards.context_guard import ContextGuard
from factory.guards.events import GuardLog


def _event(tool: str, text: str):
    return SimpleNamespace(tool_use={"name": tool}, result={"content": [{"text": text}]})


def test_shell_result_is_truncated_head_and_tail():
    log = GuardLog()
    g = ContextGuard(100, log)
    ev = _event("shell_in_workspace", "a" * 60 + "MIDDLE" + "b" * 60)
    g.after_tool(ev)
    text = ev.result["content"][0]["text"]
    assert "truncated by context_guard" in text and "MIDDLE" not in text
    assert text.startswith("a" * 50) and text.endswith("b" * 50)
    assert log.has("context_guard", "tool_result_truncated")


def test_fetch_url_is_exempt():
    log = GuardLog()
    g = ContextGuard(100, log)
    body = "x" * 500
    ev = _event("fetch_url", body)
    g.after_tool(ev)
    assert ev.result["content"][0]["text"] == body
    assert not log.has("context_guard")


def test_short_result_untouched():
    g = ContextGuard(100, GuardLog())
    ev = _event("read_file", "short")
    g.after_tool(ev)
    assert ev.result["content"][0]["text"] == "short"
