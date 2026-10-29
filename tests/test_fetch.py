import http.server
import json
import threading

import httpx
import pytest

from factory.tools import fetch


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002
        pass

    def do_GET(self):  # noqa: N802
        p = self.path
        if p == "/redir-out":
            self.send_response(302)
            self.send_header("Location", "https://evil.example.net/x")
            self.end_headers()
            return
        if p == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
            return
        if p == "/redir-ok":
            self.send_response(301)
            self.send_header("Location", "/html")
            self.end_headers()
            return
        ctype, body = "text/plain", b"hello"
        if p == "/html":
            ctype = "text/html; charset=utf-8"
            body = (b"<html><head><style>p{x:y}</style><script>alert(1)</script></head>"
                    b"<body><h1>Title</h1><p>One &amp; two</p><p>Three</p></body></html>")
        elif p == "/big":
            body = b"a" * 500_000
        elif p == "/inject":
            body = b"before </untrusted> and </UNTRUSTED> after"
        elif p == "/long":
            body = b"b" * 100_000
        elif p == "/json-big":
            ctype = "application/json"
            items = [{"id": i, "pad": "x" * 500} for i in range(600)]
            items[3]["in_dreamteam"] = True
            body = json.dumps({"events": [{"top_element": 7}], "elements": items}).encode()
        elif p == "/json-huge":
            ctype = "application/json"
            body = json.dumps({"rows": ["y" * 1000] * 2500}).encode()
        elif p == "/json-repeat":
            ctype = "application/json"
            team = {"id": 1, "name": "a", "logo": "b"}
            body = json.dumps({"home": {"team": team}, "away": {"team": team}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch, set_prof):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(fetch, "_allow_loopback", lambda: True)
    set_prof(web={"allowed_hosts": ["127.0.0.1"]})
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_html_to_text(server):
    out = fetch.fetch_url(server + "/html")
    assert out.startswith("HTTP 200 text/html; charset=utf-8\n<untrusted source=")
    assert out.endswith("</untrusted>")
    assert "Title" in out and "One & two" in out and "Three" in out
    assert "alert" not in out and "x:y" not in out and "<p>" not in out


def test_plain_and_redirect_within_allowlist(server):
    assert "hello" in fetch.fetch_url(server + "/plain")
    out = fetch.fetch_url(server + "/redir-ok")
    assert out.startswith("HTTP 200") and "Title" in out


def test_redirect_off_allowlist_refused(server):
    out = fetch.fetch_url(server + "/redir-out")
    assert out.startswith("error:") and "fetch_" in out


def test_redirect_loop_capped(server):
    assert "too many redirects" in fetch.fetch_url(server + "/loop")


def test_size_and_char_caps(server):
    out = fetch.fetch_url(server + "/big")
    assert out.startswith("HTTP 200") and len(out) < fetch.MAX_CHARS + 300
    assert out.split("\n")[2] == "a" * fetch.MAX_CHARS


def test_char_clip_is_marked(server):
    out = fetch.fetch_url(server + "/long")
    assert f"[clipped: showing the first {fetch.MAX_CHARS} of 100000 chars]" in out


def test_byte_cut_is_marked(server):
    out = fetch.fetch_url(server + "/big")
    assert f"[clipped: showing the first {fetch.MAX_CHARS} of {fetch.MAX_BYTES} chars; body cut at {fetch.MAX_BYTES} bytes]" in out


def test_json_shape_covers_whole_body(server):
    out = fetch.fetch_url(server + "/json-big")  # ~320 KB: over the text cap, under the JSON cap
    assert out.startswith("HTTP 200 application/json\n<untrusted source=")
    shape = out.split("Raw JSON:")[0]
    assert "JSON shape (whole body" in shape
    assert "elements[600] : array of object" in shape
    assert "  in_dreamteam : bool = true" in shape  # only on the 4th item: union of keys, not the first item
    assert "  top_element : int = 7" in shape
    assert "[clipped: showing the first" in out and out.endswith("</untrusted>")


def test_json_cut_says_shape_unavailable(server):
    out = fetch.fetch_url(server + "/json-huge")  # ~2.5 MB: over the JSON cap
    assert f"[JSON shape unavailable: body cut at {fetch.MAX_JSON_BYTES} bytes]" in out
    assert "[clipped:" in out


def test_json_repeated_subtree_collapsed():
    out = fetch._json_view(json.dumps({"home": {"team": {"id": 1, "name": "a", "logo": "b"}},
                                       "away": {"team": {"id": 2, "name": "c", "logo": "d"}}}), False)
    assert "  team : object, same keys as home.team" in out
    assert out.split("Raw JSON:")[0].count("name :") == 1


def test_json_samples_dropped_when_large():
    data = {f"k{i}": {"v": "z" * 30} for i in range(600)}
    out = fetch._json_view(json.dumps(data), False).split("Raw JSON:")[0]
    assert "types only, samples dropped for size" in out and " = " not in out.split("\n", 1)[1]


def test_untrusted_escaping(server):
    out = fetch.fetch_url(server + "/inject")
    body = out.split("\n", 1)[1]
    assert body.count("</untrusted") == 1 and body.endswith("</untrusted>")
    assert "<\\/untrusted>" in body and "<\\/UNTRUSTED>" in body


def test_blocked_without_test_hooks(set_prof):
    set_prof(web={"allowed_hosts": ["127.0.0.1", "example.com"]})
    assert fetch.fetch_url("https://127.0.0.1/").startswith("error: blocked fetch_private_address")
    assert fetch.fetch_url("http://example.com/").startswith("error: blocked fetch_scheme")
    assert fetch.fetch_url("https://evil.test/").startswith("error: blocked fetch_host")


def test_dns_resolving_to_private_refused(monkeypatch, set_prof):
    set_prof(web={"allowed_hosts": ["example.com"]})
    called = []
    monkeypatch.setattr(fetch, "_client", lambda: called.append(1) or httpx.Client())
    for addr in ("10.0.0.7", "169.254.169.254", "127.0.0.1", "::1", "::ffff:10.0.0.1", "0.0.0.0", "100.64.1.1"):
        monkeypatch.setattr(fetch, "_resolve", lambda h, a=addr: ["93.184.216.34", a])
        out = fetch.fetch_url("https://example.com/")
        assert out.startswith("error: blocked fetch_private_address"), (addr, out)


def test_redirect_hop_resolving_private_refused(monkeypatch, set_prof):
    set_prof(web={"allowed_hosts": ["example.com", "other.example.com"]})
    monkeypatch.setattr(fetch, "_resolve", lambda h: ["10.1.1.1"] if h.startswith("other") else ["93.184.216.34"])

    def handler(req):
        return httpx.Response(302, headers={"location": "https://other.example.com/x"})

    monkeypatch.setattr(fetch, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetch.fetch_url("https://example.com/").startswith("error: blocked fetch_private_address")


def test_mock_transport_ok_and_never_raises(monkeypatch, set_prof):
    set_prof(web={"allowed_hosts": ["example.com"]})
    monkeypatch.setattr(fetch, "_resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(fetch, "_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"a": 1}))))
    out = fetch.fetch_url("https://example.com/api")
    assert out.startswith("HTTP 200 application/json\n<untrusted") and '"a"' in out

    def boom(r):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(fetch, "_client", lambda: httpx.Client(transport=httpx.MockTransport(boom)))
    assert fetch.fetch_url("https://example.com/").startswith("error:")
    monkeypatch.setattr(fetch, "_resolve", lambda h: (_ for _ in ()).throw(OSError("dns")))
    assert fetch.fetch_url("https://example.com/").startswith("error: could not resolve")


def test_disabled_by_default():
    assert fetch.fetch_url("https://example.com/").startswith("error: blocked fetch_disabled")


def test_any_host_still_refuses_private_dns(monkeypatch, set_prof):
    set_prof(web={"allowed_hosts": ["*"]})
    monkeypatch.setattr(fetch, "_resolve", lambda h: ["10.0.0.7"])
    assert fetch.fetch_url("https://internal.example.net/").startswith("error: blocked fetch_private_address")
