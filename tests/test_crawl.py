# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
"""Bounded same-site crawl tool against a fake Crawl4AI /crawl (stdlib only, no network)."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import crawl
import provider as prov

SITE = {
    "/": ["/a", "/b", "https://evil.example/off", "/a#frag"],
    "/a": ["/a1", "/"],
    "/b": [],
    "/a1": ["/a2"],
}


class FakeCrawl:
    def __init__(self):
        self.requests = []
        self.mode = "ok"
        self.redirect_to = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                url = body["urls"][0]
                path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
                path = path.split("#")[0]
                if outer.mode == "blocked" and path == "/b":
                    raw = json.dumps({"detail": "Blocked by anti-bot protection: Cloudflare JS challenge"}).encode()
                    self.send_response(502)
                else:
                    item = {"url": url, "success": True,
                            "markdown": {"raw_markdown": f"# Page {path}\n\nbody", "fit_markdown": f"# Page {path}\n\nfit"},
                            "links": {"internal": [{"href": h} for h in SITE.get(path, [])]}}
                    if outer.redirect_to and path == "/a":
                        item["redirected_url"] = outer.redirect_to
                    raw = json.dumps({"success": True, "results": [item]}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(raw)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def srv():
    s = FakeCrawl()
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, srv):
    values = {"CRAWL4AI_URL": srv.url, "CRAWL4AI_API_TOKEN": "c4-secret"}
    monkeypatch.setattr(prov, "get_secret_str", lambda name, default="": values.get(name, default))

    async def safe(url):
        return True

    monkeypatch.setattr(crawl, "async_is_safe_url", safe)
    monkeypatch.setattr(crawl, "check_website_access", lambda u: None)
    return values


def call(url, **args):
    return json.loads(asyncio.run(crawl.crawl4ai_crawl_tool({"url": url, **args})))


def site(srv, path=""):
    return "https://site.example" + path


def paths(res):
    return [r["url"].replace("https://site.example", "") or "/" for r in res["results"]]


def test_basic_depth_follows_one_level_same_site(env, srv):
    res = call("https://site.example/")
    assert paths(res) == ["/", "/a", "/b"]            # /a1 is level 2, evil.example is off-site, /a#frag deduped
    assert all("error" not in r for r in res["results"])
    assert srv.requests[0]["path"] == "/crawl" and srv.requests[0]["auth"] == "Bearer c4-secret"
    assert srv.requests[0]["body"]["urls"] == ["https://site.example/"]
    assert "deep_crawl_strategy" not in json.dumps(srv.requests[0]["body"])


def test_advanced_depth_and_max_pages(env, srv):
    assert paths(call("https://site.example/", depth="advanced")) == ["/", "/a", "/b", "/a1"]
    assert len(call("https://site.example/", depth="advanced", max_pages=2)["results"]) == 2


def test_max_pages_is_clamped(env, srv):
    assert len(call("https://site.example/", max_pages=0)["results"]) == 1
    assert len(call("https://site.example/", max_pages=999)["results"]) <= 20


def test_blocked_page_becomes_error_entry_others_continue(env, srv):
    srv.mode = "blocked"
    res = call("https://site.example/")
    by = {p: r for p, r in zip(paths(res), res["results"])}
    assert "anti-bot" in by["/b"]["error"] and "error" not in by["/a"]
    assert "c4-secret" not in json.dumps(res)


def test_seed_blocked_by_policy_makes_no_request(env, srv, monkeypatch):
    async def unsafe(url):
        return False

    monkeypatch.setattr(crawl, "async_is_safe_url", unsafe)
    res = call("https://site.example/")
    assert res["success"] is False and "private or internal" in res["error"] and srv.requests == []


def test_website_policy_blocks_seed(env, srv, monkeypatch):
    monkeypatch.setattr(crawl, "check_website_access", lambda u: {"message": "blocked by policy"})
    res = call("https://site.example/")
    assert res["success"] is False and "policy" in res["error"] and srv.requests == []


def test_result_that_leaves_site_is_rejected(env, srv):
    srv.redirect_to = "https://elsewhere.example/landing"
    res = call("https://site.example/")
    a = [r for r in res["results"] if r["url"].endswith("/a")][0]
    assert "left the seed site" in a["error"]


def test_missing_url_and_unconfigured(env, srv):
    assert call("")["success"] is False
    env["CRAWL4AI_URL"] = ""
    assert call("https://site.example/")["success"] is False


def test_max_chars_applies_to_crawled_pages(env, srv):
    env["CRAWL4AI_MAX_CHARS"] = "2000"
    assert all(len(r["content"]) < 2200 for r in call("https://site.example/")["results"])


def test_schema_and_registration():
    import importlib.util
    import pathlib
    import sys
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "hermes_crawl4ai_pkg", root / "__init__.py", submodule_search_locations=[str(root)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["hermes_crawl4ai_pkg"] = pkg
    try:
        spec.loader.exec_module(pkg)
    finally:
        for k in [k for k in sys.modules if k.startswith("hermes_crawl4ai_pkg")]:
            sys.modules.pop(k, None)

    class Ctx:
        providers, tools = [], []

        def register_web_search_provider(self, p):
            self.providers.append(p)

        def register_tool(self, **kw):
            self.tools.append(kw)

    ctx = Ctx()
    pkg.register(ctx)
    assert [p.name for p in ctx.providers] == ["crawl4ai"]
    t = ctx.tools[0]
    assert t["name"] == "crawl4ai_crawl" and t["toolset"] == "crawl4ai" and t["is_async"] is True
    assert t["schema"]["parameters"]["required"] == ["url"] and callable(t["handler"]) and callable(t["check_fn"])
