# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
# Crawl4AI v0.9.5 server on top of the existing Playwright v1.64.1 image (shares its browser layers).
FROM mcr.microsoft.com/playwright:v1.64.1-noble
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 PYTHONFAULTHANDLER=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PATH=/opt/venv/bin:$PATH PYTHON_ENV=production C4AI_VERSION=0.9.5 CRAWL4AI_NO_CLOUD_NOTICE=1
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv redis-server supervisor curl procps \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
# Pin playwright first so pip cannot pull a version that mismatches the bundled browsers (revision 1248).
RUN pip install "playwright==1.64.1"
COPY src/ /tmp/project/
RUN pip install /tmp/project && pip install -r /tmp/project/deploy/docker/requirements.txt \
    && (pip check || echo "NOTE: upstream pin conflicts (same as official image); non-fatal") \
    && python -c "import importlib.metadata as m;v=m.version('playwright');assert v=='1.64.1',v;print('playwright',v)" \
    && rm -rf /tmp/project
RUN ln -s /opt/venv/bin/gunicorn /usr/local/bin/gunicorn \
    && groupadd -r appuser && useradd --no-log-init -r -g appuser -m appuser
WORKDIR /app
COPY src/deploy/docker/ /app/
RUN rm -rf /app/tests && chown -R root:root /app && chmod -R a-w /app \
    && mkdir -p /var/lib/redis /var/log/redis /var/lib/crawl4ai/outputs /home/appuser/.cache \
    && chown -R appuser:appuser /var/lib/redis /var/log/redis /var/lib/crawl4ai /home/appuser \
    && chmod 700 /var/lib/crawl4ai/outputs
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:11235/health || exit 1
USER appuser
EXPOSE 11235
CMD ["bash", "entrypoint.sh"]
