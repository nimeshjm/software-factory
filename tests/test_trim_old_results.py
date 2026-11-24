"""Older tool results are cut to a preview while the most recent ones and research evidence stay whole."""
import json

from strands import Agent

from factory.fake_model import FakeModel
from factory.guards.context_guard import build_context_kwargs
from factory.tools.files import read_file


def _texts(agent, tool_name):
    names = {}
    for m in agent.messages:
        for b in m["content"]:
            if "toolUse" in b:
                names[b["toolUse"]["toolUseId"]] = b["toolUse"]["name"]
    out = []
    for m in agent.messages:
        for b in m["content"]:
            if "toolResult" in b and names.get(b["toolResult"]["toolUseId"]) == tool_name:
                out.append("".join(c.get("text", "") for c in b["toolResult"]["content"]))
    return out


def _run(monkeypatch, tmp_path, ctx, reads=10):
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    (tmp_path / "big.txt").write_text("\n".join(f"line {i} " + "x" * 60 for i in range(100)))
    script = [{"tool": "read_file", "input": {"path": "big.txt"}}] * reads + [{"text": "done"}]
    agent = Agent(model=FakeModel(script=script), tools=[read_file], callback_handler=None, **build_context_kwargs(ctx))
    agent("go")
    return _texts(agent, "read_file")


def test_older_results_are_trimmed_recent_kept(monkeypatch, tmp_path):
    ctx = {"trim_old_results": {"preserve_recent": 3, "threshold_tokens": 400, "preview_tokens": 120}}
    texts = _run(monkeypatch, tmp_path, ctx)
    assert len(texts) == 10
    trimmed = [t.startswith("[Truncated") for t in texts]
    assert trimmed[-3:] == [False, False, False]  # the three most recent stay whole
    assert all(trimmed[:5])  # older ones were cut (the newest unpreserved one may wait for the next pass)
    assert len(texts[0]) < 1500 < len(texts[-1])


def test_without_the_setting_nothing_small_is_trimmed(monkeypatch, tmp_path):
    texts = _run(monkeypatch, tmp_path, {})
    assert not any(t.startswith("[Truncated") for t in texts)
