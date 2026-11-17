# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
"""Provider tests against a local fake Crawl4AI server (stdlib only, no network)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import provider as prov


class FakeServer:
    def __init__(self):
        self.requests = []
        self.mode = "ok"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                if outer.mode == "401":
                    self.send_response(401); self.end_headers(); return
                if outer.mode == "502":
                    self.send_response(502); self.end_headers(); return
                payload = {"nonjson": b"not json", "fail": json.dumps({"success": False, "markdown": ""}).encode(),
                           "badshape": json.dumps({"success": True, "markdown": {"x": 1}}).encode()}.get(
                    outer.mode, json.dumps({"url": body["url"], "success": True,
                                            "markdown": "# Hello Title\n\nbody text"}).encode())
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(payload)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch):
    values = {}
    monkeypatch.setattr(prov, "get_provider_env", lambda name: values.get(name, ""))
    return values


def test_flags_and_availability(env):
    p = prov.Crawl4AIWebExtractProvider()
    assert (p.name, p.supports_search(), p.supports_extract()) == ("crawl4ai", False, True)
    assert not p.is_available()
    env["CRAWL4AI_URL"] = "http://127.0.0.1:11235/"
    assert p.is_available()


@pytest.mark.parametrize("bad", ["ftp://h", "http://u:p@h:1", "http://h?x=1", "http://h#f", "http://h:99999", "http://h x", "nohost"])
def test_invalid_urls_rejected(env, bad):
    env["CRAWL4AI_URL"] = bad
    assert not prov.Crawl4AIWebExtractProvider().is_available()


def test_success_sends_f_and_bearer(env, server):
    env.update(CRAWL4AI_URL=server.url, CRAWL4AI_API_TOKEN="s3cret")
    out = prov.Crawl4AIWebExtractProvider().extract(["https://example.com/a"])
    assert out[0]["title"] == "Hello Title" and out[0]["content"].endswith("body text")
    assert out[0]["raw_content"] == out[0]["content"] and out[0]["metadata"]["sourceURL"] == "https://example.com/a"
    req = server.requests[0]
    assert req["path"] == "/md" and req["body"] == {"url": "https://example.com/a", "f": "fit"}
    assert req["auth"] == "Bearer s3cret"


def test_filter_raw_and_invalid_fallback(env, server):
    env.update(CRAWL4AI_URL=server.url, CRAWL4AI_FILTER="raw")
    prov.Crawl4AIWebExtractProvider().extract(["https://e.com"])
    env["CRAWL4AI_FILTER"] = "bogus"
    prov.Crawl4AIWebExtractProvider().extract(["https://e.com"])
    assert [r["body"]["f"] for r in server.requests] == ["raw", "fit"]
    assert server.requests[0]["auth"] is None


def test_401_hint_does_not_leak_token(env, server):
    env.update(CRAWL4AI_URL=server.url, CRAWL4AI_API_TOKEN="topsecret")
    server.mode = "401"
    err = prov.Crawl4AIWebExtractProvider().extract(["https://e.com"])[0]["error"]
    assert "401" in err and "CRAWL4AI_API_TOKEN" in err and "topsecret" not in err


@pytest.mark.parametrize("mode,fragment", [("502", "HTTP 502"), ("ok", None), ("fail", "failure"), ("badshape", "failure")])
def test_error_modes(env, server, mode, fragment):
    env["CRAWL4AI_URL"] = server.url
    server.mode = mode
    r = prov.Crawl4AIWebExtractProvider().extract(["https://e.com"])[0]
    if fragment is None:
        assert "error" not in r
    else:
        assert fragment in r["error"] and r["content"] == ""


def test_non_json_response(env, server):
    env["CRAWL4AI_URL"] = server.url
    server.mode = "nonjson"
    r = prov.Crawl4AIWebExtractProvider().extract(["https://e.com"])[0]
    assert r["error"] == "Crawl4AI returned a non-JSON response"


def test_unreachable_and_unset(env):
    p = prov.Crawl4AIWebExtractProvider()
    assert "CRAWL4AI_URL" in p.extract(["https://e.com"])[0]["error"]
    env["CRAWL4AI_URL"] = "http://127.0.0.1:1"
    assert p.extract(["https://e.com"])[0]["error"] == "Could not reach Crawl4AI"


def test_non_http_url_skipped_and_order_kept(env, server):
    env["CRAWL4AI_URL"] = server.url
    out = prov.Crawl4AIWebExtractProvider().extract(["file:///etc/passwd", "https://e.com"])
    assert "Only http(s)" in out[0]["error"] and out[1]["url"] == "https://e.com"
    assert len(server.requests) == 1


def test_register_hooks_provider():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "hermes_crawl4ai_pkg", root / "__init__.py", submodule_search_locations=[str(root)])
    pkg = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["hermes_crawl4ai_pkg"] = pkg  # required for the relative `.provider` import
    try:
        spec.loader.exec_module(pkg)
    finally:
        sys.modules.pop("hermes_crawl4ai_pkg", None)
        sys.modules.pop("hermes_crawl4ai_pkg.provider", None)

    class Ctx:
        registered = []

        def register_web_search_provider(self, p):
            self.registered.append(p)

    ctx = Ctx()
    pkg.register(ctx)
    assert [p.name for p in ctx.registered] == ["crawl4ai"]
