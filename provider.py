# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
"""Crawl4AI web-extract provider for Hermes.

Extract-only: Crawl4AI renders pages in a browser and returns markdown from its
``/md`` endpoint. Pair it with a search-capable backend (e.g. SearXNG) via::

    web:
      search_backend: searxng
      extract_backend: crawl4ai

Environment (read through Hermes' config-aware lookup, never logged)::

    CRAWL4AI_URL=http://127.0.0.1:11235     # required
    CRAWL4AI_API_TOKEN=<bearer token>       # required by Crawl4AI >= 0.9 unless loopback-only
    CRAWL4AI_FILTER=fit                     # optional: fit (default) | raw
    CRAWL4AI_TIMEOUT=60                     # optional: seconds per URL (5-300)
    CRAWL4AI_VERIFY_STATUS=on               # optional: off disables the HTTP-status check of short pages
    CRAWL4AI_MAX_CHARS=0                    # optional: truncate each page (2000-200000; 0 = off)

Opt-in fallback for pages Crawl4AI reports as blocked by anti-bot protection (public URLs only)::

    CRAWL4AI_FALLBACK=firecrawl             # off unless set
    FIRECRAWL_API_KEY=<key>                 # secret; required for the fallback
    CRAWL4AI_FALLBACK_MAX_PER_CALL=3        # optional
    CRAWL4AI_FALLBACK_MAX_PER_DAY=20        # optional, per process
    CRAWL4AI_FALLBACK_DENY_DOMAINS=a.com,b.org   # optional: never sent to the fallback
"""

from __future__ import annotations

import datetime
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from agent.secret_scope import get_secret_str
from agent.web_search_provider import WebSearchProvider
from tools.url_safety import is_safe_url
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

_FILTERS = {"fit", "raw"}
_DEFAULT_TIMEOUT = 60.0
_FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"  # fixed: the API key is never sent elsewhere
_FIRECRAWL_TIMEOUT = 60.0
_THIN_CHARS = 1500
_VERIFY_BELOW = 1500  # markdown shorter than this is checked for an HTTP error status
_MAX_DETAIL = 200
_CHALLENGE_RE = re.compile(r"just a moment|verify you are human|enable javascript and cookies|attention required", re.I)
_BLOCKED_RE = re.compile(r"anti-?bot", re.I)
_FB_LOCK = threading.Lock()
_FB_STATE: Dict[str, Any] = {"day": None, "count": 0}  # in-memory daily counter (resets on restart)


def _env(name: str) -> str:
    """Read a setting through Hermes' profile-scoped credential route (catalog rule 11).

    Never reads ``.env``/``os.environ`` directly. On a multiplexed gateway with no profile
    scope bound, ``get_secret_str`` raises ``UnscopedSecretError`` and we let it propagate
    rather than guess another profile's value.
    """
    return get_secret_str(name).strip()


def _base_url() -> str:
    """Return the validated base URL, or ``""`` when unset/invalid."""
    value = _env("CRAWL4AI_URL").rstrip("/")
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in ("http", "https") and parts.hostname
            and parts.username is None and parts.password is None
            and not parts.query and not parts.fragment and not re.search(r"\s", value)
        )
        parts.port  # raises ValueError on a bad port
    except ValueError:
        return ""
    return value if valid else ""


def _timeout() -> float:
    try:
        return min(max(float(_env("CRAWL4AI_TIMEOUT") or _DEFAULT_TIMEOUT), 5.0), 300.0)
    except ValueError:
        return _DEFAULT_TIMEOUT


def _title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _entry(url: str, title: str = "", content: str = "", error: str = "") -> Dict[str, Any]:
    if error:
        return {"url": url, "title": "", "content": "", "error": error}
    return {"url": url, "title": title, "content": content, "raw_content": content,
            "metadata": {"sourceURL": url, "title": title}}


def _int_setting(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return min(max(int(_env(name)), lo), hi)
    except ValueError:
        return default


def _max_chars() -> int:
    """0 disables truncation; otherwise clamp to 2000-200000."""
    try:
        value = int(_env("CRAWL4AI_MAX_CHARS") or 0)
    except ValueError:
        return 0
    return 0 if value <= 0 else min(max(value, 2000), 200000)


def _truncate(entry: Dict[str, Any]) -> Dict[str, Any]:
    limit = _max_chars()
    if not limit or "error" in entry:
        return entry
    content = entry.get("content") or ""
    if len(content) > limit:
        entry["content"] = content[:limit] + f"\n\n[Truncated after {limit} characters]"
        entry["raw_content"] = content[:limit]
    return entry


def _detail(response: Any) -> str:
    """Server error detail (Crawl4AI: ``{"detail": "..."}``), trimmed. Never contains our credentials."""
    try:
        data = response.json()
    except ValueError:
        return ""
    value = data.get("detail") if isinstance(data, dict) else None
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    return " ".join(str(value).split())[:_MAX_DETAIL] if value else ""


def _verify_enabled() -> bool:
    return _env("CRAWL4AI_VERIFY_STATUS").lower() not in ("off", "0", "false", "no")


def _page_status(client: Any, base: str, headers: Dict[str, str], url: str) -> Optional[int]:
    """HTTP status of the page via /crawl (/md reports none). None when it cannot be determined."""
    payload = {"urls": [url], "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
               "crawler_config": {"type": "CrawlerRunConfig", "params": {"cache_mode": "bypass"}}}
    try:
        resp = client.post(f"{base}/crawl", json=payload, headers=headers)
        if resp.status_code >= 400:
            return None
        items = resp.json().get("results")
        code = items[0].get("status_code") if isinstance(items, list) and items else None
        return code if isinstance(code, int) else None
    except Exception:  # noqa: BLE001 - verification is best effort and must never fail the extract
        return None


def _is_blocked(detail: str) -> bool:
    return bool(_BLOCKED_RE.search(detail))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _deny_domains() -> List[str]:
    return [d.strip().lower().lstrip(".") for d in _env("CRAWL4AI_FALLBACK_DENY_DOMAINS").split(",") if d.strip()]


def _fallback_enabled() -> bool:
    return _env("CRAWL4AI_FALLBACK").lower() == "firecrawl" and bool(_env("FIRECRAWL_API_KEY"))


def _fallback_skip_reason(url: str) -> str:
    """Why this URL must not leave for the third-party fallback ('' = allowed)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "not an http(s) URL"
    if parts.query or parts.fragment or parts.username or parts.password:
        return "URL has a query string, fragment, or credentials"
    host = parts.hostname.lower()
    if any(host == d or host.endswith("." + d) for d in _deny_domains()):
        return "domain is on the deny list"
    if not is_safe_url(url):
        return "URL targets a private or internal address"
    blocked = check_website_access(url)
    if blocked:
        return str(blocked.get("message") or "blocked by website policy")[:_MAX_DETAIL]
    return ""


def _reserve_daily_slot() -> bool:
    limit = _int_setting("CRAWL4AI_FALLBACK_MAX_PER_DAY", 20, 0, 1000)
    today = datetime.date.today().isoformat()
    with _FB_LOCK:
        if _FB_STATE["day"] != today:
            _FB_STATE.update(day=today, count=0)
        if _FB_STATE["count"] >= limit:
            return False
        _FB_STATE["count"] += 1
        return True


def _firecrawl(client: Any, url: str, reason: str) -> Dict[str, Any]:
    """One Firecrawl scrape of a public URL. Errors keep the original Crawl4AI reason visible."""
    import httpx

    def fail(msg: str) -> Dict[str, Any]:
        return _entry(url, error=f"{reason}; fallback: {msg}")

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_env('FIRECRAWL_API_KEY')}"}
    try:
        resp = client.post(_FIRECRAWL_URL, json={"url": url, "formats": ["markdown"]}, headers=headers,
                           timeout=_FIRECRAWL_TIMEOUT)
        if resp.status_code >= 400:
            return fail(f"Firecrawl returned HTTP {resp.status_code}")
        data = resp.json()
    except httpx.TimeoutException:
        return fail("Firecrawl request timed out")
    except httpx.RequestError:
        return fail("could not reach Firecrawl")
    except ValueError:
        return fail("Firecrawl returned a non-JSON response")
    body = data.get("data") if isinstance(data, dict) else None
    markdown = body.get("markdown") if isinstance(body, dict) else None
    if not isinstance(data, dict) or not data.get("success") or not isinstance(markdown, str):
        return fail("Firecrawl reported failure or an invalid response")
    if not markdown.strip():
        return fail("Firecrawl returned an empty page")
    if _CHALLENGE_RE.search(markdown[:3000]):
        return fail("Firecrawl returned a challenge page")
    thin = len(markdown) < _THIN_CHARS
    note = "> Fetched via Firecrawl Cloud because the local Crawl4AI was blocked by anti-bot protection."
    if thin:
        note += " The result is short; it may be an index page or incomplete."
    entry = _entry(url, _title(markdown, url), f"{note}\n\n{markdown}")
    entry["metadata"].update(extracted_via="firecrawl-cloud", fallback_reason="anti-bot", thin=thin)
    logger.info("Crawl4AI blocked on %s; served via Firecrawl fallback", _host(url))
    return entry


class Crawl4AIWebExtractProvider(WebSearchProvider):
    """Extract page markdown through a self-hosted Crawl4AI server."""

    @property
    def name(self) -> str:
        return "crawl4ai"

    @property
    def display_name(self) -> str:
        return "Crawl4AI"

    def is_available(self) -> bool:  # cheap: no network
        return bool(_base_url())

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        import httpx

        base = _base_url()
        if not base:
            return [_entry(u, error="CRAWL4AI_URL is not set or is not a valid http(s) URL") for u in urls]
        token = _env("CRAWL4AI_API_TOKEN")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        mode = (_env("CRAWL4AI_FILTER") or "fit").lower()
        mode = mode if mode in _FILTERS else "fit"
        fallback = _fallback_enabled()
        per_call = _int_setting("CRAWL4AI_FALLBACK_MAX_PER_CALL", 3, 0, 50)
        used = 0

        results: List[Dict[str, Any]] = []
        with httpx.Client(timeout=_timeout()) as client:
            for url in urls:
                entry, blocked = self._fetch_local(client, base, headers, url, mode)
                if blocked and fallback:
                    reason = entry["error"]
                    skip = _fallback_skip_reason(url)
                    if skip:
                        entry = _entry(url, error=f"{reason} (fallback skipped: {skip})")
                    elif used >= per_call or not _reserve_daily_slot():
                        entry = _entry(url, error=f"{reason} (fallback skipped: budget reached)")
                    else:
                        used += 1
                        entry = _firecrawl(client, url, reason)
                results.append(_truncate(entry))
        return results

    @staticmethod
    def _fetch_local(client: Any, base: str, headers: Dict[str, str], url: str, mode: str) -> Tuple[Dict[str, Any], bool]:
        """Fetch one URL from Crawl4AI. Returns (entry, blocked_by_anti_bot)."""
        import httpx

        if urlsplit(url).scheme not in ("http", "https"):
            return _entry(url, error="Only http(s) URLs are supported"), False
        try:
            resp = client.post(f"{base}/md", json={"url": url, "f": mode}, headers=headers)
            if resp.status_code >= 400:
                code, detail = resp.status_code, _detail(resp)
                if _is_blocked(detail):
                    return _entry(url, error=f"Crawl4AI: {detail} (HTTP {code})"), True
                hint = " (check CRAWL4AI_API_TOKEN)" if code in (401, 403) else ""
                extra = f": {detail}" if detail else ""
                return _entry(url, error=f"Crawl4AI returned HTTP {code}{hint}{extra}"), False
            data = resp.json()
        except httpx.TimeoutException:
            return _entry(url, error="Crawl4AI request timed out"), False
        except httpx.RequestError:
            return _entry(url, error="Could not reach Crawl4AI"), False
        except ValueError:
            return _entry(url, error="Crawl4AI returned a non-JSON response"), False

        markdown = data.get("markdown") if isinstance(data, dict) else None
        if not isinstance(markdown, str) or not data.get("success"):
            return _entry(url, error="Crawl4AI reported failure or an invalid response"), False
        if len(markdown) < _VERIFY_BELOW and _verify_enabled():
            code = _page_status(client, base, headers, url)
            if code is not None and code >= 400:
                return _entry(url, error=f"Page returned HTTP {code} (Crawl4AI /md reported success)"), False
        return _entry(url, _title(markdown, url), markdown), False

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Crawl4AI",
            "badge": "free · self-hosted",
            "tag": "Browser-rendered markdown from your own Crawl4AI server.",
            "env_vars": [
                {"key": "CRAWL4AI_URL", "prompt": "Crawl4AI URL (e.g. http://127.0.0.1:11235)",
                 "url": "https://github.com/unclecode/crawl4ai"},
                {"key": "CRAWL4AI_API_TOKEN", "secret": True, "prompt": "Crawl4AI bearer token", "url": ""},
            ],
        }
