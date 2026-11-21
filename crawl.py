# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
#
# Adapted from hermes-web-crawl4ai (c) 2026 axAilotl, MIT License, commit 3879756
# (https://github.com/axAilotl/hermes-web-crawl4ai). See THIRD_PARTY_NOTICES.md.
# Changes: settings come from the profile-scoped credential route (no config.yaml block), a single
# flat /crawl call per page, errors are typed with the server's anti-bot detail, no Firecrawl fallback.
"""Bounded same-site crawl tool (``crawl4ai_crawl``) backed by the same Crawl4AI server.

Links are followed here, one single-page ``/crawl`` call at a time, so every URL is checked against
Hermes' URL-safety and website policy BEFORE it reaches the service (Crawl4AI >= 0.9 rejects a
server-side ``deep_crawl_strategy`` in untrusted requests).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Dict, Optional
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from tools.url_safety import async_is_safe_url, normalize_url_for_request
from tools.website_policy import check_website_access

try:  # loaded as a package by Hermes, or as a top-level module by the tests
    from .provider import _base_url, _detail, _entry, _env, _int_setting, _is_blocked, _timeout, _title, _truncate
except ImportError:  # pragma: no cover
    from provider import _base_url, _detail, _entry, _env, _int_setting, _is_blocked, _timeout, _title, _truncate

logger = logging.getLogger(__name__)

MAX_CRAWL_PAGES = 20

CRAWL4AI_CRAWL_SCHEMA = {
    "name": "crawl4ai_crawl",
    "description": (
        "Crawl a bounded set of pages under one public website using the user's self-hosted Crawl4AI "
        "service. Use this only when several linked pages are needed; for one known page, use "
        "web_extract instead. Pages blocked by anti-bot protection come back as per-page errors."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public HTTP(S) seed URL to crawl."},
            "depth": {"type": "string", "enum": ["basic", "advanced"], "default": "basic",
                      "description": "Basic follows one level; advanced follows up to two levels."},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": MAX_CRAWL_PAGES, "default": 10,
                          "description": "Maximum pages returned from the crawl."},
        },
        "required": ["url"],
    },
}


def is_available() -> bool:
    return bool(_base_url())


async def _target_error(url: str) -> Optional[str]:
    """Return a policy error for a model- or backend-provided target URL."""
    if not await async_is_safe_url(url):
        return "Blocked: URL targets a private or internal network address"
    blocked = check_website_access(url)
    if blocked:
        return str(blocked.get("message") or "Blocked by website policy")
    return None


def _is_same_site(seed_url: str, candidate_url: str) -> bool:
    """Accept the seed host and its subdomains; reject unrelated crawl results."""
    seed = (urlsplit(seed_url).hostname or "").lower().rstrip(".")
    cand = (urlsplit(candidate_url).hostname or "").lower().rstrip(".")
    return bool(seed and cand and (cand == seed or cand.endswith("." + seed)))


async def _post_crawl(client: httpx.AsyncClient, base: str, url: str) -> Dict[str, Any]:
    token = _env("CRAWL4AI_API_TOKEN")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "urls": [url],
        "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
        "crawler_config": {"type": "CrawlerRunConfig", "params": {"cache_mode": "bypass"}},
    }
    resp = await client.post(f"{base}/crawl", json=payload, headers=headers)
    if resp.status_code >= 400:
        detail = _detail(resp)
        hint = " (check CRAWL4AI_API_TOKEN)" if resp.status_code in (401, 403) and not _is_blocked(detail) else ""
        raise ValueError(f"Crawl4AI returned HTTP {resp.status_code}{hint}" + (f": {detail}" if detail else ""))
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Crawl4AI returned a non-object JSON response")
    return data


def _document(raw: Dict[str, Any], fallback_url: str) -> Dict[str, Any]:
    items = raw.get("results", raw.get("result", []))
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    url = str(item.get("redirected_url") or item.get("url") or fallback_url)
    md = item.get("markdown")
    if isinstance(md, dict):
        content = md.get("fit_markdown") or md.get("raw_markdown") or ""
    else:
        content = md if isinstance(md, str) else ""
    if item.get("success") is False or not content:
        reason = str(item.get("error_message") or item.get("error") or "Crawl4AI returned no content")
        return _entry(url, error=" ".join(reason.split())[:200])
    return _entry(url, _title(content, url), content)


async def crawl4ai_crawl_tool(args: Dict[str, Any], **_: Any) -> str:
    """Run a bounded same-site crawl; never raises, returns a JSON string."""
    seed = str((args or {}).get("url") or "").strip()
    try:
        base = _base_url()
        if not base:
            return json.dumps({"success": False, "error": "CRAWL4AI_URL is not set or is not a valid http(s) URL"})
        if not seed:
            return json.dumps({"success": False, "error": "A URL is required"})
        seed = normalize_url_for_request(seed)
        error = await _target_error(seed)
        if error:
            return json.dumps({"success": False, "error": error, "url": seed})

        max_depth = 2 if args.get("depth") == "advanced" else 1
        try:
            max_pages = max(1, min(int(args.get("max_pages", 10)), MAX_CRAWL_PAGES))
        except (TypeError, ValueError):
            max_pages = 10
        deadline = _int_setting("CRAWL4AI_CRAWL_TIMEOUT", 180, 30, 300)

        pending = deque([(seed, 0)])
        seen = {urldefrag(seed)[0]}
        results = []
        async with asyncio.timeout(deadline):
            async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False) as client:
                while pending and len(results) < max_pages:
                    target, level = pending.popleft()
                    policy_error = await _target_error(target)
                    if policy_error:
                        results.append(_entry(target, error=policy_error))
                        continue
                    try:
                        raw = await _post_crawl(client, base, target)
                        doc = _document(raw, target)
                    except (httpx.HTTPError, ValueError) as exc:
                        # never include the exception text of transport errors: it can echo the URL only
                        results.append(_entry(target, error=str(exc) if isinstance(exc, ValueError)
                                              else "Could not reach Crawl4AI"))
                        continue
                    if "error" not in doc and not _is_same_site(seed, doc["url"]):
                        doc = _entry(target, error="Blocked: crawl result left the seed site")
                    elif "error" not in doc:
                        post = await _target_error(doc["url"])
                        if post:
                            doc = _entry(target, error=post)
                    results.append(_truncate(doc))
                    if "error" in doc or level >= max_depth:
                        continue
                    items = raw.get("results", raw.get("result", []))
                    links = (items[0].get("links") if isinstance(items, list) and items and isinstance(items[0], dict) else None)
                    links = links.get("internal", []) if isinstance(links, dict) else []
                    if not isinstance(links, list):
                        continue
                    for link in links:
                        href = link.get("href") if isinstance(link, dict) else link
                        if not isinstance(href, str) or not href.strip():
                            continue
                        try:
                            candidate = urldefrag(normalize_url_for_request(urljoin(doc["url"], href)))[0]
                        except ValueError:
                            continue
                        if candidate in seen or not _is_same_site(seed, candidate):
                            continue
                        if len(seen) >= max_pages:  # bound the queue too: a page can hold thousands of links
                            break
                        seen.add(candidate)
                        pending.append((candidate, level + 1))
        return json.dumps({"results": results}, ensure_ascii=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - tool errors are returned to the model
        logger.warning("Crawl4AI crawl failed: %s", type(exc).__name__)
        return json.dumps({"success": False, "error": f"Crawl4AI crawl failed: {type(exc).__name__}", "url": seed})
