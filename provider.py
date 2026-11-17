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
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List
from urllib.parse import urlsplit

from agent.web_search_provider import WebSearchProvider, get_provider_env

logger = logging.getLogger(__name__)

_FILTERS = {"fit", "raw"}
_DEFAULT_TIMEOUT = 60.0


def _base_url() -> str:
    """Return the validated base URL, or ``""`` when unset/invalid."""
    value = get_provider_env("CRAWL4AI_URL").rstrip("/")
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
        return min(max(float(get_provider_env("CRAWL4AI_TIMEOUT") or _DEFAULT_TIMEOUT), 5.0), 300.0)
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
        token = get_provider_env("CRAWL4AI_API_TOKEN")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        mode = (get_provider_env("CRAWL4AI_FILTER") or "fit").lower()
        mode = mode if mode in _FILTERS else "fit"

        results: List[Dict[str, Any]] = []
        with httpx.Client(timeout=_timeout()) as client:
            for url in urls:
                if urlsplit(url).scheme not in ("http", "https"):
                    results.append(_entry(url, error="Only http(s) URLs are supported"))
                    continue
                try:
                    resp = client.post(f"{base}/md", json={"url": url, "f": mode}, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    hint = " (check CRAWL4AI_API_TOKEN)" if code in (401, 403) else ""
                    results.append(_entry(url, error=f"Crawl4AI returned HTTP {code}{hint}"))
                    continue
                except httpx.TimeoutException:
                    results.append(_entry(url, error="Crawl4AI request timed out"))
                    continue
                except httpx.RequestError:
                    results.append(_entry(url, error="Could not reach Crawl4AI"))
                    continue
                except ValueError:
                    results.append(_entry(url, error="Crawl4AI returned a non-JSON response"))
                    continue

                markdown = data.get("markdown") if isinstance(data, dict) else None
                if not isinstance(markdown, str) or not data.get("success"):
                    results.append(_entry(url, error="Crawl4AI reported failure or an invalid response"))
                    continue
                results.append(_entry(url, _title(markdown, url), markdown))
        return results

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
