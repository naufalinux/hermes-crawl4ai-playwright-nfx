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
    monkeypatch.setattr(prov, "get_secret_str", lambda name, default="": values.get(name, default))
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

        def register_tool(self, **kw):
            pass

    ctx = Ctx()
    pkg.register(ctx)
    assert [p.name for p in ctx.registered] == ["crawl4ai"]


# --- Credential route (catalog rule 11): real agent.secret_scope, no monkeypatching of the reader ---

SETTING_NAMES = ("CRAWL4AI_URL", "CRAWL4AI_API_TOKEN", "CRAWL4AI_FILTER", "CRAWL4AI_TIMEOUT")


@pytest.fixture
def clean_env(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def scope():
    """Install a real profile secret scope; always reset it."""
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    tokens = []

    def bind(mapping):
        tokens.append(set_secret_scope(mapping))

    yield bind
    for token in reversed(tokens):
        reset_secret_scope(token)


def test_settings_come_from_bound_secret_scope(clean_env, scope, server):
    scope({"CRAWL4AI_URL": server.url, "CRAWL4AI_API_TOKEN": "scoped-token", "CRAWL4AI_FILTER": "raw"})
    p = prov.Crawl4AIWebExtractProvider()
    assert p.is_available()
    out = p.extract(["https://e.com"])
    assert "error" not in out[0]
    assert server.requests[0]["auth"] == "Bearer scoped-token" and server.requests[0]["body"]["f"] == "raw"


def test_single_profile_scope_miss_falls_back_to_process_env(clean_env, scope, server, monkeypatch):
    # Single-profile semantics: the scope is an overlay, process env (systemd / op run) still works.
    scope({})
    monkeypatch.setenv("CRAWL4AI_URL", server.url)
    assert prov.Crawl4AIWebExtractProvider().is_available()


def test_multiplex_scope_miss_never_uses_another_profiles_env(clean_env, scope, server, monkeypatch):
    import agent.secret_scope as ss
    monkeypatch.setattr(ss, "_MULTIPLEX_ACTIVE", True)
    monkeypatch.setenv("CRAWL4AI_URL", server.url)  # belongs to the process' profile, not the served one
    scope({"CRAWL4AI_API_TOKEN": "profile-b"})
    p = prov.Crawl4AIWebExtractProvider()
    assert not p.is_available()
    assert "CRAWL4AI_URL" in p.extract(["https://e.com"])[0]["error"]
    assert server.requests == []


def test_multiplex_without_scope_fails_closed(clean_env, server, monkeypatch):
    import agent.secret_scope as ss
    monkeypatch.setattr(ss, "_MULTIPLEX_ACTIVE", True)
    monkeypatch.setenv("CRAWL4AI_URL", server.url)
    p = prov.Crawl4AIWebExtractProvider()
    with pytest.raises(ss.UnscopedSecretError):
        p.is_available()
    with pytest.raises(ss.UnscopedSecretError):
        p.extract(["https://e.com"])
    assert server.requests == []


def test_provider_reads_credentials_only_through_secret_scope():
    """Regression guard for rule 11: no env/dotenv/credential-file reads in the plugin source."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("provider.py", "crawl.py", "__init__.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & {"os", "dotenv"}, name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in {"os", "dotenv"}, name
                assert "get_provider_env" not in {a.name for a in node.names}, name
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"environ", "getenv"}, name
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                raise AssertionError(f"{name}: open() call")
    src = (root / "provider.py").read_text()
    assert "get_secret_str" in src
