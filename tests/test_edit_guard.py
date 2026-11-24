"""EditGuard nudges an implement or revise run that keeps reading without editing."""
from types import SimpleNamespace

from factory.guards.edit_guard import EditGuard
from factory.guards.events import GuardLog


def _event(name, tokens=0, status="success"):
    inv = SimpleNamespace(usage={"totalTokens": tokens})
    agent = SimpleNamespace(event_loop_metrics=SimpleNamespace(latest_agent_invocation=inv))
    return SimpleNamespace(tool_use={"name": name}, result={"status": status, "content": [{"text": "x"}]}, agent=agent)


def _texts(ev):
    return [c["text"] for c in ev.result["content"]]


def test_nudges_after_the_read_limit_and_again_at_twice_it():
    log = GuardLog()
    g = EditGuard("revise", {"total_tokens": 900_000}, log, read_limit=3)
    evs = [_event("read_file") for _ in range(7)]
    for ev in evs:
        g.after_tool(ev)
    nudged = [i for i, ev in enumerate(evs) if len(_texts(ev)) == 2]
    assert nudged == [2, 5]
    assert "what the review asks for" in _texts(evs[2])[1] and _texts(evs[2])[1].startswith("[edit_guard] 3 reads")
    assert [e["rule"] for e in log.events] == ["nudge", "nudge"]


def test_nudges_on_tokens_before_the_read_limit():
    g = EditGuard("implement", {"total_tokens": 1000}, GuardLog(), token_fraction=0.35, read_limit=50)
    ev = _event("search_files", tokens=400)
    g.after_tool(ev)
    assert "400 of the phase's tokens used" in _texts(ev)[1] and "the plan's steps" in _texts(ev)[1]


def test_a_successful_edit_stops_it():
    g = EditGuard("revise", {"total_tokens": 900_000}, GuardLog(), read_limit=2)
    g.after_tool(_event("read_file"))
    g.after_tool(_event("edit_file"))
    ev = _event("read_file")
    g.after_tool(ev)
    assert len(_texts(ev)) == 1


def test_a_failed_edit_does_not_count():
    g = EditGuard("revise", {"total_tokens": 900_000}, GuardLog(), read_limit=2)
    g.after_tool(_event("edit_file", status="error"))
    g.after_tool(_event("read_file"))
    ev = _event("read_file")
    g.after_tool(ev)
    assert len(_texts(ev)) == 2


def test_other_phases_and_tools_are_ignored():
    g = EditGuard("research", {"total_tokens": 10}, GuardLog(), read_limit=1)
    ev = _event("read_file", tokens=100)
    g.after_tool(ev)
    assert len(_texts(ev)) == 1
    g2 = EditGuard("revise", {"total_tokens": 10}, GuardLog(), read_limit=1)
    ev2 = _event("run_checks", tokens=100)
    g2.after_tool(ev2)
    assert len(_texts(ev2)) == 1
