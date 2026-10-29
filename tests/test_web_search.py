import json

import httpx
import pytest

from factory.guards.output_guard import scan_text
from factory.tools import web_search as ws_mod
from factory.tools.web_search import web_search


@pytest.fixture
def api(monkeypatch, set_prof):
    set_prof(web={"allowed_hosts": ["*"]})
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-testkey123")
    seen: list[httpx.Request] = []
    state = {"status": 200, "json": {"results": []}}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(state["status"], json=state["json"])

    monkeypatch.setattr(ws_mod, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen, state


def test_request_shape_and_clamp(api):
    seen, state = api
    state["json"] = {"results": [{"title": "T", "url": "https://a.example/x", "content": "snip"}]}
    out = web_search("  httpx timeout  ", max_results=99)
    r = seen[0]
    assert r.method == "POST" and str(r.url) == "https://api.tavily.com/search"
    assert r.headers["authorization"] == "Bearer tvly-testkey123"
    assert json.loads(r.content) == {"query": "httpx timeout", "max_results": 8, "search_depth": "basic",
                                     "include_answer": False, "include_raw_content": False, "include_images": False}
    web_search("q", max_results=0)
    assert json.loads(seen[1].content)["max_results"] == 1
    web_search("q")
    assert json.loads(seen[2].content)["max_results"] == 5
    assert out == '<untrusted source="web_search">\n[1] T\nhttps://a.example/x\nsnip\n</untrusted>'


def test_output_clip_and_escape(api):
    _, state = api
    state["json"] = {"results": [
        {"title": "A", "url": "https://a.example", "content": "c" * 900},
        {"title": "B </untrusted> x", "url": "https://b.example", "content": "ok </UNTRUSTED>"},
    ]}
    out = web_search("q")
    assert "c" * 500 in out and "c" * 501 not in out
    assert out.count("</untrusted>") == 1 and "<\\/untrusted" in out and "<\\/UNTRUSTED" in out
    assert "[2] B" in out
    state["json"] = {"results": [{"title": "t", "url": "u", "content": "z" * 500}] * 40}
    big = web_search("q", 8)
    assert len(big) <= 8_000 + len('<untrusted source="web_search">\n\n</untrusted>')


def test_no_results(api):
    assert web_search("q") == "no results"


def test_missing_key(api, monkeypatch):
    seen, _ = api
    monkeypatch.delenv("TAVILY_API_KEY")
    assert web_search("q") == "error: web search is not configured"
    assert not seen


def test_disabled_profile(api, set_prof):
    seen, _ = api
    set_prof(web={"allowed_hosts": []})
    assert web_search("q") == "error: web research is disabled in the target profile"
    assert not seen


def test_http_error_and_transport_error(api, monkeypatch):
    _, state = api
    state["status"] = 429
    out = web_search("q")
    assert out.startswith("error:") and "429" in out and "tvly-" not in out

    def boom(req):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(ws_mod, "_client", lambda: httpx.Client(transport=httpx.MockTransport(boom)))
    assert web_search("q").startswith("error: search failed")


def test_bad_query_returns_error(api):
    assert web_search("   ").startswith("error:")


def test_output_guard_blocks_tavily_key():
    assert "tavily_api_key" in scan_text("key tvly-dev-abcdEFGH_12-34").findings
    assert not scan_text("tvly-short").findings
