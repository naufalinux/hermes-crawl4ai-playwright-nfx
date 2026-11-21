# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
"""Typed errors, Firecrawl fallback, and limits. Fake Crawl4AI + fake Firecrawl, stdlib only, no network."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import provider as prov

BLOCK = "Blocked by anti-bot protection: Cloudflare JS challenge"
BIG = "# Real Article\n\n" + ("Vitamin D and sarcopenia evidence. " * 80)


class Fake:
    """One server playing both Crawl4AI (/md) and Firecrawl (/v2/scrape)."""

    def __init__(self):
        self.md = []          # (path, body, auth)
        self.fc = []
        self.md_mode = "ok"   # ok | blocked | blocked200 | timeoutish | 401 | 502plain | 500detail
        self.fc_mode = "ok"   # ok | thin | challenge | empty | 402 | fail | big
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(raw)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                auth = self.headers.get("Authorization")
                if self.path == "/md":
                    outer.md.append((self.path, body, auth))
                    m = outer.md_mode
                    if m == "blocked":
                        return self._send(502, {"detail": BLOCK})
                    if m == "401":
                        return self._send(401, {"detail": "bad token"})
                    if m == "502plain":
                        self.send_response(502); self.end_headers(); return
                    if m == "500detail":
                        return self._send(500, {"detail": "browser crashed"})
                    if m == "big":
                        return self._send(200, {"success": True, "markdown": "# T\n\n" + "x" * 5000})
                    return self._send(200, {"success": True, "markdown": "# Local Page\n\nlocal body"})
                if self.path == "/v2/scrape":
                    outer.fc.append((body, auth))
                    m = outer.fc_mode
                    if m == "402":
                        return self._send(402, {"error": "Payment required"})
                    if m == "fail":
                        return self._send(200, {"success": False, "error": "nope"})
                    md = {"ok": BIG, "thin": "# Hub\n\nshort", "challenge": "Just a moment... verify you are human " + "x" * 3000,
                          "empty": ""}.get(m, BIG)
                    return self._send(200, {"success": True, "data": {"markdown": md, "metadata": {"statusCode": 200}}})
                self.send_response(404); self.end_headers()

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def fake():
    s = Fake()
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, fake):
    values = {"CRAWL4AI_URL": fake.url, "CRAWL4AI_API_TOKEN": "c4-secret"}
    monkeypatch.setattr(prov, "get_secret_str", lambda name, default="": values.get(name, default))
    monkeypatch.setattr(prov, "_FIRECRAWL_URL", fake.url + "/v2/scrape")
    monkeypatch.setattr(prov, "is_safe_url", lambda u: True)
    monkeypatch.setattr(prov, "check_website_access", lambda u: None)
    prov._FB_STATE.update(day=None, count=0)
    return values


def on(env):
    env.update(CRAWL4AI_FALLBACK="firecrawl", FIRECRAWL_API_KEY="fc-secret")


def run(urls):
    return prov.Crawl4AIWebExtractProvider().extract(urls)


# ---- typed errors -------------------------------------------------------------------------

def test_blocked_error_is_typed_and_has_detail(env, fake):
    fake.md_mode = "blocked"
    err = run(["https://a.example/x"])[0]["error"]
    assert "blocked" in err.lower() and "anti-bot" in err and "Cloudflare" in err
    assert "c4-secret" not in err and err != "Crawl4AI returned HTTP 502"


def test_other_detail_is_kept_without_token(env, fake):
    fake.md_mode = "500detail"
    err = run(["https://a.example/x"])[0]["error"]
    assert "HTTP 500" in err and "browser crashed" in err and "c4-secret" not in err


def test_plain_502_without_body_keeps_old_message(env, fake):
    fake.md_mode = "502plain"
    assert "HTTP 502" in run(["https://a.example/x"])[0]["error"]


# ---- fallback: trigger -------------------------------------------------------------------

def test_fallback_off_by_default(env, fake):
    fake.md_mode = "blocked"
    env["FIRECRAWL_API_KEY"] = "fc-secret"          # key alone must not enable it
    out = run(["https://a.example/x"])[0]
    assert "error" in out and fake.fc == []


def test_fallback_needs_key(env, fake):
    fake.md_mode = "blocked"
    env["CRAWL4AI_FALLBACK"] = "firecrawl"
    out = run(["https://a.example/x"])[0]
    assert "error" in out and fake.fc == []


def test_fallback_success_is_marked(env, fake):
    on(env); fake.md_mode = "blocked"
    r = run(["https://a.example/x"])[0]
    assert "error" not in r and "Real Article" in r["content"]
    assert r["metadata"]["extracted_via"] == "firecrawl-cloud"
    assert "Firecrawl" in r["content"].splitlines()[0]
    assert r["title"] == "Real Article"
    body, auth = fake.fc[0]
    assert body["url"] == "https://a.example/x" and body["formats"] == ["markdown"] and auth == "Bearer fc-secret"
    assert list(body) == ["url", "formats"] or set(body) <= {"url", "formats", "timeout"}


@pytest.mark.parametrize("mode", ["401", "502plain", "500detail"])
def test_no_fallback_for_non_blocked_failures(env, fake, mode):
    on(env); fake.md_mode = mode
    assert "error" in run(["https://a.example/x"])[0] and fake.fc == []


def test_no_fallback_when_unreachable_or_timeout(env, fake):
    on(env); env["CRAWL4AI_URL"] = "http://127.0.0.1:1"
    assert run(["https://a.example/x"])[0]["error"] == "Could not reach Crawl4AI" and fake.fc == []


def test_no_fallback_when_local_succeeds(env, fake):
    on(env)
    assert "error" not in run(["https://a.example/x"])[0] and fake.fc == []


# ---- fallback: privacy filters ------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://a.example/x?token=1", "https://a.example/x#frag", "https://user:***@a.example/x"])
def test_urls_with_query_fragment_userinfo_never_leave(env, fake, url):
    on(env); fake.md_mode = "blocked"
    err = run([url])[0]["error"]
    assert fake.fc == [] and "fallback skipped" in err


def test_deny_domains(env, fake):
    on(env); fake.md_mode = "blocked"; env["CRAWL4AI_FALLBACK_DENY_DOMAINS"] = "secret.example, other.org"
    err = run(["https://sub.secret.example/p"])[0]["error"]
    assert fake.fc == [] and "fallback skipped" in err
    assert "error" not in run(["https://ok.example/p"])[0]


def test_private_or_policy_blocked_targets_skipped(env, fake, monkeypatch):
    on(env); fake.md_mode = "blocked"
    monkeypatch.setattr(prov, "is_safe_url", lambda u: False)
    assert "fallback skipped" in run(["https://a.example/x"])[0]["error"] and fake.fc == []
    monkeypatch.setattr(prov, "is_safe_url", lambda u: True)
    monkeypatch.setattr(prov, "check_website_access", lambda u: {"message": "blocked by policy"})
    assert "fallback skipped" in run(["https://a.example/x"])[0]["error"] and fake.fc == []


# ---- fallback: budgets --------------------------------------------------------------------

def test_per_call_cap(env, fake):
    on(env); fake.md_mode = "blocked"
    out = run([f"https://a.example/{i}" for i in range(5)])
    ok = [r for r in out if "error" not in r]
    assert len(ok) == 3 and len(fake.fc) == 3
    assert all("budget" in r["error"] for r in out if "error" in r)


def test_per_day_cap_and_override(env, fake):
    on(env); fake.md_mode = "blocked"; env["CRAWL4AI_FALLBACK_MAX_PER_DAY"] = "2"; env["CRAWL4AI_FALLBACK_MAX_PER_CALL"] = "10"
    out = run([f"https://a.example/{i}" for i in range(4)])
    assert len([r for r in out if "error" not in r]) == 2 and len(fake.fc) == 2
    out2 = run(["https://a.example/z"])
    assert "budget" in out2[0]["error"] and len(fake.fc) == 2


def test_bad_budget_values_use_defaults(env, fake):
    on(env); fake.md_mode = "blocked"; env["CRAWL4AI_FALLBACK_MAX_PER_CALL"] = "abc"
    out = run([f"https://a.example/{i}" for i in range(5)])
    assert len(fake.fc) == 3 and len(out) == 5


# ---- fallback: validating Firecrawl results -----------------------------------------------

@pytest.mark.parametrize("mode,frag", [("challenge", "challenge"), ("empty", "empty"), ("fail", "Firecrawl"), ("402", "402")])
def test_bad_firecrawl_results_are_errors(env, fake, mode, frag):
    on(env); fake.md_mode = "blocked"; fake.fc_mode = mode
    r = run(["https://a.example/x"])[0]
    assert "error" in r and frag in r["error"] and "fc-secret" not in r["error"]
    assert "anti-bot" in r["error"]      # original reason stays visible


def test_thin_result_is_flagged(env, fake):
    on(env); fake.md_mode = "blocked"; fake.fc_mode = "thin"
    r = run(["https://a.example/x"])[0]
    assert "error" not in r and r["metadata"]["thin"] is True and "short" in r["content"]


def test_unreachable_firecrawl_is_error_without_key(env, fake, monkeypatch):
    on(env); fake.md_mode = "blocked"
    monkeypatch.setattr(prov, "_FIRECRAWL_URL", "http://127.0.0.1:1/v2/scrape")
    err = run(["https://a.example/x"])[0]["error"]
    assert "Firecrawl" in err and "fc-secret" not in err


# ---- max chars ----------------------------------------------------------------------------

def test_max_chars_truncates_and_default_does_not(env, fake):
    fake.md_mode = "big"
    assert len(run(["https://a.example/x"])[0]["content"]) > 5000
    env["CRAWL4AI_MAX_CHARS"] = "2000"
    c = run(["https://a.example/x"])[0]["content"]
    assert len(c) < 2200 and "Truncated" in c


# ---- no key in logs -----------------------------------------------------------------------

def test_key_never_logged(env, fake, caplog):
    on(env); fake.md_mode = "blocked"
    with caplog.at_level("DEBUG"):
        run(["https://a.example/x"])
    assert "fc-secret" not in caplog.text and "c4-secret" not in caplog.text
    assert "a.example" in caplog.text and "/x" not in caplog.text    # host only, not the full URL


# ---- HTTP status verification for short pages (404 reported as success by /md) -----------------

class StatusServer:
    """/md always succeeds; /crawl reports a status code per path."""

    def __init__(self):
        self.calls = []
        self.status = {"/missing": 404, "/gone": 410, "/boom": 500}
        self.crawl_fails = False
        self.md_text = "# Not Found\n\nThis page doesn't exist."
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append((self.path, body))
                if self.path == "/md":
                    raw = json.dumps({"success": True, "markdown": outer.md_text}).encode()
                    self.send_response(200)
                elif outer.crawl_fails:
                    raw = b"{}"; self.send_response(500)
                else:
                    path = "/" + body["urls"][0].split("/", 3)[3]
                    raw = json.dumps({"success": True, "results": [{"status_code": outer.status.get(path, 200)}]}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(raw)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def st(monkeypatch):
    s = StatusServer()
    values = {"CRAWL4AI_URL": s.url}
    monkeypatch.setattr(prov, "get_secret_str", lambda name, default="": values.get(name, default))
    s.env = values
    yield s
    s.httpd.shutdown()


@pytest.mark.parametrize("path,code", [("/missing", 404), ("/gone", 410), ("/boom", 500)])
def test_short_page_with_error_status_is_error(st, path, code):
    r = run([f"https://a.example{path}"])[0]
    assert f"HTTP {code}" in r["error"] and r["content"] == ""


def test_short_page_with_200_stays_success(st):
    r = run(["https://a.example/ok"])[0]
    assert "error" not in r and "Not Found" in r["content"]


def test_status_check_failure_keeps_result(st):
    st.crawl_fails = True
    assert "error" not in run(["https://a.example/missing"])[0]


def test_long_page_skips_status_check(st):
    st.md_text = "# Real\n\n" + "word " * 600
    r = run(["https://a.example/missing"])[0]
    assert "error" not in r and [p for p, _ in st.calls] == ["/md"]


def test_verify_can_be_disabled(st):
    st.env["CRAWL4AI_VERIFY_STATUS"] = "off"
    assert "error" not in run(["https://a.example/missing"])[0] and [p for p, _ in st.calls] == ["/md"]


def test_error_status_never_triggers_fallback(st):
    st.env.update(CRAWL4AI_FALLBACK="firecrawl", FIRECRAWL_API_KEY="fc-secret")
    assert "HTTP 404" in run(["https://a.example/missing"])[0]["error"]
