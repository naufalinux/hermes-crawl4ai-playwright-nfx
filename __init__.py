# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
"""hermes-crawl4ai-playwright-nfx: registers a Crawl4AI web-extract backend."""

from __future__ import annotations

from .provider import Crawl4AIWebExtractProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(Crawl4AIWebExtractProvider())
