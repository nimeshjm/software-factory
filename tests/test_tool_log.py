import json
from types import SimpleNamespace

from factory import run as run_mod
from factory.guards import ToolCallLog
from factory.guards.tool_log import MAX_CALLS


def _ev(name, inp, text, cancel=None):
    return SimpleNamespace(tool_use={"name": name, "input": inp}, cancel_message=cancel,
                           result={"status": "ok", "content": [{"text": text}]})


def _one(name, inp, text, cancel=None):
    tl = ToolCallLog()
    tl.after(_ev(name, inp, text, cancel))
    return tl.calls[0]


def test_blocked_error_ok():
    msg = "[tool_guard] Blocked git_push: no"
    assert _one("shell_in_workspace", {"command": "git push"}, msg, msg)["status"] == "blocked"
    assert _one("shell_in_workspace", {"command": "x"}, msg)["status"] == "blocked"
    assert _one("read_file", {"path": "a"}, "error: nope")["status"] == "error"
    c = _one("read_file", {"path": "a.py"}, "hello")
    assert c["status"] == "ok" and c["target"] == "a.py" and c["http_status"] is None


def test_fetch_http_status_and_final_url():
    text = 'HTTP 200 text/html\n<untrusted source="https://b.example/final">\nx\n</untrusted>'
    c = _one("fetch_url", {"url": "https://a.example/"}, text)
    assert c["http_status"] == 200 and c["target"] == "https://b.example/final" and c["status"] == "ok"
    c = _one("fetch_url", {"url": "https://a.example/"},
             'HTTP 404 text/plain\n<untrusted source="https://a.example/">\n</untrusted>')
    assert c["http_status"] == 404 and c["status"] == "ok"
    c = _one("fetch_url", {"url": "https://a.example/"}, "error: could not resolve a.example")
    assert c["http_status"] is None and c["status"] == "error" and c["target"] == "https://a.example/"


def test_targets():
    assert _one("web_search", {"query": "q1"}, "r")["target"] == "q1"
    assert _one("search_files", {"pattern": "foo", "path_glob": "*.py"}, "r")["target"] == "foo *.py"
    assert _one("other", {"x": 1}, "r")["target"] == ""


def test_redaction_and_clip():
    tok = "ghp_" + "a" * 36
    c = _one("shell_in_workspace", {"command": f"curl -H {tok} x"}, "ok")
    assert tok not in c["target"] and "[REDACTED]" in c["target"]
    assert len(_one("shell_in_workspace", {"command": "x" * 500}, "ok")["target"]) == 200


def test_cap():
    tl = ToolCallLog()
    for _ in range(MAX_CALLS + 5):
        tl.after(_ev("read_file", {"path": "a"}, "ok"))
    assert len(tl.calls) == MAX_CALLS


def test_stderr_line(capsys):
    _one("fetch_url", {"url": "https://a.example/"}, "HTTP 200 t\nbody")
    assert "[tool] fetch_url ok 200 https://a.example/" in capsys.readouterr().err
    _one("read_file", {"path": "p"}, "ok")
    assert "[tool] read_file ok - p" in capsys.readouterr().err


def test_never_raises():
    ToolCallLog().after(SimpleNamespace())


def test_run_writes_tool_calls(tmp_path, ws, monkeypatch):
    from tests.test_e2e import _inputs
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps([{"tool": "read_file", "input": {"path": "../etc/passwd"}},
                              {"tool": "list_files", "input": {"path": "."}}]))
    monkeypatch.setenv("FACTORY_FAKE_SCRIPT", str(sp))
    out = tmp_path / "out"
    run_mod.main(["--agent", "triage", "--phase", "triage", "--inputs", str(_inputs(tmp_path)),
                  "--out", str(out), "--fake-model"])
    res = json.loads((out / "result.json").read_text())
    calls = [c for c in res["tool_calls"] if c["name"] in ("read_file", "list_files")]
    assert [c["name"] for c in calls] == ["read_file", "list_files"]
    assert calls[0]["status"] == "blocked" and calls[0]["http_status"] is None
