import json
import subprocess

import httpx
import pytest

from factory import config as C
from factory.schemas import ALL_MODELS, RunResult, dump_schemas, proposal_model_for
from factory.tools import TOOL_REGISTRY, build_tools, files, honeycomb, shell


def test_resolve_overrides():
    cfg = C.load_config()
    r = C.resolve_run_spec(cfg, "builder", "research")
    assert r.tools == ["read_file", "list_files", "search_files"] and r.max_model_calls == 25
    assert r.model_id == "gemini-2.5-flash" and r.params["thinking_config"]["thinking_budget"] == 0
    imp = C.resolve_run_spec(cfg, "builder", "implement")
    assert "edit_file" in imp.tools and imp.limits["turns"] == 60 and imp.timeout_minutes == 40
    bf = C.resolve_run_spec(cfg, "bug-fixer", "research")
    assert "shell_in_workspace" in bf.tools
    t = C.resolve_run_spec(cfg, "triage", "triage")
    assert t.model_id == "gemini-2.5-flash-lite" and t.timeout_seconds == 600
    with pytest.raises(C.ConfigError):
        C.resolve_run_spec(cfg, "triage", "plan")
    with pytest.raises(C.ConfigError):
        C.resolve_run_spec(cfg, "ghost", "x")


def test_every_configured_tool_exists():
    cfg = C.load_config()
    for a in cfg["agents"].values():
        for t in a["tools"] + [x for o in a.get("phase_overrides", {}).values() for x in o.get("tools", [])]:
            assert t in TOOL_REGISTRY
    assert len(build_tools(["read_file"])) == 1
    with pytest.raises(KeyError):
        build_tools(["raw_shell"])


def test_schema_round_trip(tmp_path):
    cfg = C.load_config()
    for agent, a in cfg["agents"].items():
        for ph in a["phases"]:
            m = proposal_model_for(agent, ph)
            assert m.model_json_schema()
    from factory.fake_model import CANNED
    for name, data in CANNED.items():
        m = ALL_MODELS[name]
        assert m.model_validate_json(m(**data).model_dump_json()) == m(**data)
    dump_schemas(tmp_path)
    assert (tmp_path / "BuildProposal.schema.json").is_file()
    assert RunResult(run_id="1", agent="a", phase="p", success=True, stop_reason="end_turn")


def test_file_tools(ws):
    assert files.edit_file("src/a.py", "create", file_text="one\ntwo\n").startswith("created")
    assert "1\tone" in files.read_file("src/a.py")
    assert files.edit_file("src/a.py", "str_replace", old_str="two", new_str="2") == "edited src/a.py"
    assert "must match exactly once" in files.edit_file("src/a.py", "str_replace", old_str="zzz", new_str="y")
    files.edit_file("src/a.py", "insert", insert_line=1, insert_text="mid")
    assert (ws / "src/a.py").read_text() == "one\nmid\n2\n"
    assert "src/a.py" in files.list_files(".")
    assert files.search_files("mid", "src/**/*.py") == "src/a.py:2:mid"
    assert files.search_files("nothing", "**/*") == "no matches"
    assert files.read_file("../x").startswith("error:")
    assert files.edit_file("/etc/x", "create", file_text="a").startswith("error:")


def test_shell_checks_commit(ws, set_prof):
    assert "exit_code=0" in shell.shell_in_workspace("echo hi") and "hi" in shell.shell_in_workspace("echo hi")
    assert str(ws.resolve()) in shell.shell_in_workspace("pwd")
    import os
    os.environ["SECRET_TEST"] = "s3"
    assert "s3" not in shell.shell_in_workspace("echo $SECRET_TEST")
    assert "timed out" in shell.shell_in_workspace("sleep 5", timeout=1)
    assert shell.run_checks() == "run_checks: no checks configured in factory.yml"
    set_prof(toolchain={"checks": [{"name": "typecheck", "run": "echo ok"},
                                   {"name": "lint", "run": "echo lint-bad; exit 3"},
                                   {"name": "test", "run": "echo never-run > ran.txt"}]})
    res = shell.run_checks()
    assert "checks: FAIL" in res and "lint: FAIL (exit 3)" in res and "lint-bad" in res
    assert "typecheck: PASS" in res and not (ws / "ran.txt").exists()
    set_prof(toolchain={"checks": [{"name": "a", "run": "true"}, {"name": "b", "run": "true"}]})
    assert shell.run_checks() == "checks: PASS\na: PASS (exit 0)\nb: PASS (exit 0)"
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    (ws / "f.txt").write_text("x")
    out = shell.git_commit("Add f (#1)")
    assert out.startswith("committed")
    body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=ws, capture_output=True, text=True).stdout
    assert "Co-Authored-By: software-factory <factory@users.noreply.github.com>" in body
    assert shell.git_commit("again") == "nothing to commit"


def test_honeycomb_query(monkeypatch, set_prof):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, req.headers.get("x-honeycomb-team")))
        if req.url.path.startswith("/1/queries/"):
            return httpx.Response(200, json={"id": "q1"})
        if req.method == "POST":
            return httpx.Response(200, json={"id": "r1"})
        return httpx.Response(200, json={"complete": True, "data": {"results": [{"data": {"COUNT": 3, "name": "x"}}]}})

    monkeypatch.setattr(honeycomb, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("HONEYCOMB_QUERY_KEY", "k")
    monkeypatch.setenv("FACTORY_HONEYCOMB_FORCE", "1")
    set_prof(honeycomb={"enabled": True, "dataset": "my-dataset", "environment": "prod"})
    out = json.loads(honeycomb.honeycomb_query('{"calculations":[{"op":"COUNT"}]}'))
    assert out == {"row_count": 1, "rows": [{"COUNT": 3, "name": "x"}], "truncated": False}
    assert seen[0] == ("POST", "/1/queries/my-dataset", "k")
    assert honeycomb.honeycomb_query("not json").startswith("error: invalid")
    monkeypatch.delenv("HONEYCOMB_QUERY_KEY")
    assert "not set" in honeycomb.honeycomb_query("{}")


def test_run_query_signature(monkeypatch):
    def handler(req: httpx.Request):
        if req.url.path.startswith("/1/queries/"):
            assert req.url.host == "hc.example"
            return httpx.Response(200, json={"id": "q1"})
        if req.method == "POST":
            return httpx.Response(200, json={"id": "r1"})
        return httpx.Response(200, json={"complete": True, "data": {"results": []}})

    monkeypatch.setattr(honeycomb, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    body = honeycomb.run_query({}, api_base="https://hc.example/", dataset="d", environment="e", key="k")
    assert body["complete"]


def test_honeycomb_disabled_by_profile(monkeypatch, set_prof):
    monkeypatch.setenv("HONEYCOMB_QUERY_KEY", "k")
    monkeypatch.delenv("FACTORY_HONEYCOMB_FORCE", raising=False)
    assert "disabled" in honeycomb.honeycomb_query("{}")
    set_prof(honeycomb={"enabled": True})
    assert "dataset is not set" in honeycomb.honeycomb_query("{}")


def test_skip_dirs_extended_by_profile(ws, set_prof):
    for d in ("node_modules", "build", "gen", "src"):
        (ws / d).mkdir()
        (ws / d / "f.txt").write_text("needle")
    out = files.list_files(".")
    assert "src/f.txt" in out and "gen/f.txt" in out and "node_modules" not in out and "build/" not in out
    set_prof(toolchain={"ignore_dirs": ["gen"]})
    assert "gen/f.txt" not in files.list_files(".") and "gen/f.txt" not in files.search_files("needle")
