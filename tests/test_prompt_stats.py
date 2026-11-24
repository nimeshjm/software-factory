from types import SimpleNamespace

from factory.guards.prompt_stats import PromptStats, message_parts, task_sections

TASK = "Agent: builder\n\n## Issue #1 (labels: x)\nbody\n\n## Goal\nship\n\n## open issues\n" + "x" * 400 + "\n\n## Goal\nship\n"
MESSAGES = [
    {"role": "user", "content": [{"text": TASK}]},
    {"role": "assistant", "content": [{"text": "reading"},
                                      {"toolUse": {"toolUseId": "1", "name": "read_file", "input": {"path": "a.ts"}}}]},
    {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "status": "success",
                                                 "content": [{"text": "y" * 800}]}}]},
    {"role": "assistant", "content": [{"toolUse": {"toolUseId": "2", "name": "edit_file",
                                                   "input": {"path": "a.ts", "new": "z" * 300}}}]},
]


def test_message_parts():
    p = message_parts(MESSAGES)
    assert p["task"] == len(TASK)
    assert p["tool_results"] == 800
    assert p["assistant_text"] == len("reading")
    assert 300 < p["edit_args"] < 340 and 0 < p["tool_args"] < 30
    assert p["other"] == 0


def test_task_sections_number_repeats_and_strip_label_detail():
    secs = task_sections(TASK)
    names = [h for h, _ in secs]
    assert names == ["(preamble)", "Issue #1", "Goal", "open issues", "Goal #2"]
    assert sum(n for _, n in secs) == len(TASK)
    assert dict(secs)["open issues"] > 400


class _Span:
    def __init__(self):
        self.events = []

    def add_event(self, name, attrs):
        self.events.append((name, attrs))


def _event(messages):
    registry = SimpleNamespace(get_all_tool_specs=lambda: [{"name": "read_file", "description": "d" * 96}])
    agent = SimpleNamespace(messages=messages, system_prompt="# Builder\nrules\n## Profile\n" + "p" * 200,
                            tool_registry=registry)
    return SimpleNamespace(agent=agent, projected_input_tokens=1234)


def test_prompt_event_each_request_and_sections_once(monkeypatch):
    span = _Span()
    monkeypatch.setattr("opentelemetry.trace.get_current_span", lambda: span)
    hook = PromptStats()
    hook.before_model(_event(MESSAGES[:1]))
    hook.before_model(_event(MESSAGES))
    prompts = [a for n, a in span.events if n == "factory.prompt"]
    assert [a["factory.prompt.request"] for a in prompts] == [1, 2]
    first, second = prompts
    assert first["factory.prompt.history_tokens"] == 0 and first["factory.prompt.tool_results_tokens"] == 0
    assert second["factory.prompt.tool_results_tokens"] == 200 and second["factory.prompt.messages"] == 4
    assert second["factory.prompt.task_tokens"] == (len(TASK) + 3) // 4
    assert second["factory.prompt.total_tokens"] >= sum(second[f"factory.prompt.{k}_tokens"] for k in
                                                        ("system", "tools", "task", "tool_results")) - 4
    assert second["factory.prompt.projected_tokens"] == 1234
    sections = [a for n, a in span.events if n == "factory.task_section"]
    assert {a["factory.task.part"] for a in sections} == {"system", "task"}
    assert [a["factory.task.section"] for a in sections if a["factory.task.part"] == "task"][-1] == "Goal #2"
    assert len(sections) == len(task_sections(TASK)) + 2  # sent once, not on the second request


def test_hook_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no tracer")

    monkeypatch.setattr("opentelemetry.trace.get_current_span", boom)
    PromptStats().before_model(_event(MESSAGES))
    PromptStats().before_model(SimpleNamespace(agent=None))
