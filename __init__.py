# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
"""hermes-crawl4ai-playwright-nfx: registers a Crawl4AI web-extract backend and a bounded crawl tool."""

from .crawl import CRAWL4AI_CRAWL_SCHEMA, crawl4ai_crawl_tool, is_available
from .provider import Crawl4AIWebExtractProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(Crawl4AIWebExtractProvider())
    ctx.register_tool(
        name="crawl4ai_crawl",
        toolset="crawl4ai",
        schema=CRAWL4AI_CRAWL_SCHEMA,
        handler=crawl4ai_crawl_tool,
        check_fn=is_available,
        is_async=True,
        description="Bounded same-site crawling through a self-hosted Crawl4AI service.",
        emoji="🕸️",
    )
