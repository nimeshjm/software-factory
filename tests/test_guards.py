import time
from types import SimpleNamespace

from strands.types.exceptions import ModelThrottledException

from factory.guards import ContextGuard, GuardLog, LatencyGuard, QuotaGuard
from factory.guards.context_guard import build_context_kwargs, truncate_text, truncate_tool_result
from factory.guards.output_guard import ZWSP, redact, scan_obj, scan_text


def test_quota_guard_caps_calls():
    log = GuardLog()
    q = QuotaGuard(2, log)
    evs = [SimpleNamespace(cancel=False) for _ in range(4)]
    for e in evs:
        q.before_model(e)
    assert [bool(e.cancel) for e in evs] == [False, False, True, True]
    assert evs[2].cancel == "[quota_guard] run model-call cap reached"
    assert q.calls == 2 and q.capped
    assert len([e for e in log.events if e["guard"] == "quota_guard"]) == 1


def test_quota_counts_throttles():
    q = QuotaGuard(5, GuardLog())
    q.after_model(SimpleNamespace(exception=ModelThrottledException("429")))
    q.after_model(SimpleNamespace(exception=None))
    assert q.throttles == 1


class FakeAgent:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def test_latency_guard_soft_then_hard():
    log = GuardLog()
    g = LatencyGuard(0.5, log, soft_fraction=0.2)
    agent = FakeAgent()
    g.start(agent)
    early = SimpleNamespace(end_turn=False)
    g.on_after_tools(early)
    assert early.end_turn is False
    time.sleep(0.2)
    soft = SimpleNamespace(end_turn=False)
    g.on_after_tools(soft)
    assert soft.end_turn.startswith("[latency_guard] time budget nearly spent")
    again = SimpleNamespace(end_turn=False)
    g.on_after_tools(again)
    assert again.end_turn is False  # nudge only once
    assert not agent.cancelled
    time.sleep(0.5)
    assert agent.cancelled and g.hard_fired
    assert {e["rule"] for e in log.events} == {"soft_nudge", "hard_timeout"}
    g.stop()


def test_latency_guard_stop_cancels_timers():
    g = LatencyGuard(0.2, GuardLog())
    a = FakeAgent()
    g.start(a)
    g.stop()
    time.sleep(0.4)
    assert not a.cancelled


def test_truncate_text_head_tail():
    t = "A" * 100 + "B" * 100
    out = truncate_text(t, 50)
    assert out.startswith("A" * 25) and out.endswith("B" * 25) and "150 chars truncated" in out
    assert truncate_text("short", 50) == "short"


def test_context_guard_hook():
    log = GuardLog()
    big = {"toolUseId": "1", "status": "success", "content": [{"text": "x" * 500}, {"json": {"a": 1}}]}
    ev = SimpleNamespace(result=big, tool_use={"name": "read_file"})
    ContextGuard(100, log).after_tool(ev)
    assert len(ev.result["content"][0]["text"]) < 300 and ev.result["content"][1] == {"json": {"a": 1}}
    assert log.events[0]["rule"] == "tool_result_truncated"
    same, changed = truncate_tool_result({"content": [{"text": "ok"}]}, 100)
    assert not changed


def test_context_kwargs_uses_offload():
    kw = build_context_kwargs({"offload_threshold_tokens": 2000, "summarize_utilization": 0.8, "preserve_recent": 4})
    assert "context_manager" in kw


def test_output_guard_secrets_and_mentions():
    for s in ["hcaik_abc", "ghs_abc", "ghp_abc", "github_pat_abc", "AIzaSyX",
              "-----BEGIN RSA PRIVATE KEY-----"]:
        assert scan_text(f"x {s} y").blocked, s
    r = scan_text("thanks @octocat and @org/team, mail a@b.com")
    assert not r.blocked and r.mentions_neutralised == 2
    assert f"@{ZWSP}octocat" in r.text and "a@b.com" in r.text
    assert scan_text(r.text).mentions_neutralised == 0  # idempotent
    assert "[REDACTED]" in redact("key ghp_abc123")


def test_scan_obj_skips_path_keys():
    obj, findings = scan_obj({"plan_md": "ping @bob", "files": ["node_modules/@scope/x"], "n": 1})
    assert ZWSP in obj["plan_md"] and obj["files"] == ["node_modules/@scope/x"] and not findings
    _, f = scan_obj({"a": ["ok", {"b": "ghp_zzz"}]})
    assert f == ["github_pat"]


def test_output_guard_allows_config_var_names():
    assert not scan_text("set APP_BASE_URL and read APP_TOKEN from the env").blocked


def test_output_guard_profile_patterns_add_only():
    from factory.guards.output_guard import BASELINE_SECRET_PATTERNS, patterns_for
    from factory.schemas.target_profile import TargetProfile

    prof = TargetProfile.model_validate({"secrets": {"patterns": [r"APP_TOKEN\s*=\s*\S{8,}"]}})
    assert not scan_text("APP_TOKEN=abcdefgh1234").blocked
    assert scan_text("x APP_TOKEN=abcdefgh1234", profile=prof).blocked
    assert scan_text("ghp_abc", profile=prof).blocked  # baseline still applies
    assert set(BASELINE_SECRET_PATTERNS) <= set(patterns_for(prof))
    _, f = scan_obj({"a": "APP_TOKEN=abcdefgh1234"}, prof)
    assert f
    assert "[REDACTED]" in redact("APP_TOKEN=abcdefgh1234", prof)


def test_output_guard_cli_profile(tmp_path):
    import json

    from factory.guards.output_guard import main as guard_main

    (tmp_path / "factory.json").write_text(json.dumps({"secrets": {"patterns": ["SEKRET_[0-9]+"]}}))
    (tmp_path / "t.txt").write_text("SEKRET_12345")
    assert guard_main(["--file", str(tmp_path / "t.txt")]) == 0
    assert guard_main(["--file", str(tmp_path / "t.txt"), "--profile", str(tmp_path / "factory.json")]) == 2
    assert guard_main(["--file", str(tmp_path / "t.txt"), "--profile", str(tmp_path / "missing.json")]) == 1
