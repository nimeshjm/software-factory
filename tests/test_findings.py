import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory.config import ConfigError, apply_spike_model, issue_labels, load_config, resolve_run_spec
from factory.policy.__main__ import main
from factory.policy.findings import check_findings, evidence_table

FIX = Path(__file__).parent / "fixtures"


def fetch(url, status, name="fetch_url"):
    return {"name": name, "target": url, "status": "ok", "http_status": status, "ts": "t"}


def res(*calls, phase="implement"):
    return {"phase": phase, "tool_calls": list(calls)}


def test_matching_fetch_ok():
    md = "### A\n- URL: `https://example.com/api/x`\n- HTTP status: 200\n"
    assert check_findings(md, [res(fetch("https://example.com/api/x", 200))]) == []


def test_wrong_status_fails():
    md = "### A\nhttps://example.com/api/x\nHTTP status: 200\n"
    v = check_findings(md, [res(fetch("https://example.com/api/x", 404))])
    assert len(v) == 1 and "200" in v[0]


def test_status_without_url_fails():
    v = check_findings("### A\nHTTP status: 200\n", [res(fetch("https://example.com/", 200))])
    assert len(v) == 1


def test_query_ignored_and_placeholder_dropped():
    md = "### A\n`https://example.com/s?event=<id>`\n**HTTP status**: 200\n"
    assert check_findings(md, [res(fetch("https://example.com/s?event=7", 200))]) == []


def test_section_without_status_passes():
    md = "### A\nhttps://example.com/\nHTTP status: requires API key\n\n### B\nnot fetched\n"
    assert check_findings(md, [res()]) == []


def test_status_in_other_section_does_not_count():
    md = "### A\nhttps://example.com/a\n\n### B\nhttps://example.com/b\nHTTP status: 200\n"
    assert check_findings(md, [res(fetch("https://example.com/a", 200))])


def test_multiple_statuses_each_checked():
    md = "### A\nhttps://example.com/a\nHTTP status: 200 (ok) / 400 (bad)\n"
    assert check_findings(md, [res(fetch("https://example.com/a?q=1", 400))]) == [
        "### A: claims HTTP status 200 but no fetch_url call returned 200 for a URL in this section"]
    both = [res(fetch("https://example.com/a", 400), fetch("https://example.com/a", 200))]
    assert check_findings(md, both) == []


def test_real_fixture_fails_with_espn_and_fantasy():
    md = json.loads((FIX / "spike_proposal.json").read_text())["findings_md"]
    results = [json.loads((FIX / f"spike_{p}_result.json").read_text()) for p in ("research", "implement")]
    v = check_findings(md, results)
    assert any("ESPN" in x and "200" in x for x in v)
    assert any("Fantasy" in x for x in v)
    assert not any("Liga Portugal official" in x for x in v)
    assert len(v) == 2


def test_evidence_table_and_sanitising():
    r = [res(fetch("https://example.com/x", 200), fetch("https://example.com/?u=@user", None),
             {"name": "web_search", "target": "ask @bob", "status": "ok", "http_status": None, "ts": "t"},
             {"name": "read_file", "target": "a", "status": "ok", "http_status": None, "ts": "t"},
             phase="research"), res(phase="implement")]
    t = evidence_table(r)
    assert "### research" in t and "| https://example.com/x | 200 | ok |" in t
    assert "| https://example.com/?u=@\u200buser | - | ok |" in t
    assert "- ask @​bob" in t
    assert "read_file" not in t
    assert t.split("### implement")[1].count("none") == 2


def test_cli_exit_codes(tmp_path, capsys):
    prop = tmp_path / "p.json"
    prop.write_text(json.dumps({"findings_md": "### A\nhttps://example.com/a\nHTTP status: 200\n"}))
    good = tmp_path / "r.json"
    good.write_text(json.dumps(res(fetch("https://example.com/a", 200))))
    assert main(["check-findings", "--proposal", str(prop), "--results", str(good)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "violations": []}
    missing = str(tmp_path / "nope.json")
    assert main(["check-findings", "--proposal", str(prop), "--results", missing]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert main(["evidence-table", "--results", str(good), missing]) == 0
    assert "https://example.com/a" in capsys.readouterr().out


def test_spike_model_selection(tmp_path):
    cfg = load_config()
    base = resolve_run_spec(cfg, "builder", "implement")
    for phase, key, model in (("research", "spike_research", "gemini-3.8-flash"), ("implement", "spike", "gemini-3.5-flash-lite")):
        s = apply_spike_model(cfg, resolve_run_spec(cfg, "builder", phase), ["spike"])
        assert (s.model_key, s.model_id) == (key, model) and s.params["thinking_config"]["thinking_level"] == "high"
    impl = apply_spike_model(cfg, base, ["spike"])
    res_base = resolve_run_spec(cfg, "builder", "research")
    research = apply_spike_model(cfg, res_base, ["spike"])
    assert research.fallback_model_ids == ("gemini-3.6-flash", "gemini-3-flash-preview")
    assert impl.fallback_model_ids == ("gemini-3.1-flash-lite",)
    assert (base.max_model_calls, impl.max_model_calls) == (60, 15)
    assert research.max_model_calls == min(res_base.max_model_calls, 25)
    low = {**cfg, "models": {**cfg["models"], "spike": {**cfg["models"]["spike"], "max_model_calls": 90}}}
    assert apply_spike_model(low, base, ["spike"]).max_model_calls == 60
    for key in ("spike_research", "spike"):
        broken = {**cfg, "models": {k: v for k, v in cfg["models"].items() if k != key}}
        phase = "research" if key == "spike_research" else "implement"
        with pytest.raises(ConfigError, match=key):
            apply_spike_model(broken, resolve_run_spec(cfg, "builder", phase), ["spike"])
    assert apply_spike_model(cfg, base, ["bug"]) is base
    plan = resolve_run_spec(cfg, "builder", "plan")
    assert apply_spike_model(cfg, plan, ["spike"]) is plan
    fixer = resolve_run_spec(cfg, "bug-fixer", "implement")
    assert apply_spike_model(cfg, fixer, ["spike"]) is fixer
    (tmp_path / "issue.json").write_text(json.dumps({"labels": ["spike", {"name": "bug"}]}))
    assert issue_labels(tmp_path) == ["spike", "bug"]
    assert issue_labels(tmp_path / "none") == []


def test_spike_total_tokens_cap():
    cfg = load_config()
    base = resolve_run_spec(cfg, "builder", "implement")
    assert apply_spike_model(cfg, base, ["spike"]).limits["total_tokens"] == min(base.limits["total_tokens"], 300000)
    for cap, want in ((100, 100), (10**9, base.limits["total_tokens"])):
        m = {**cfg, "models": {**cfg["models"], "spike": {**cfg["models"]["spike"], "total_tokens": cap}}}
        assert apply_spike_model(m, base, ["spike"]).limits["total_tokens"] == want
    none = {**cfg, "models": {**cfg["models"], "spike": {k: v for k, v in cfg["models"]["spike"].items() if k != "total_tokens"}}}
    assert apply_spike_model(none, base, ["spike"]).limits == base.limits
    nolimit = replace(base, limits={k: v for k, v in base.limits.items() if k != "total_tokens"})
    assert "total_tokens" not in apply_spike_model(cfg, nolimit, ["spike"]).limits


def test_claims_backed_only_by_research_fetches():
    md = "### A\nhttps://example.com/a\nHTTP status: 200\n"
    research = res(fetch("https://example.com/a", 200), phase="research")
    implement = res(phase="implement")
    assert check_findings(md, [research, implement]) == []
    assert check_findings(md, [implement]) != []
