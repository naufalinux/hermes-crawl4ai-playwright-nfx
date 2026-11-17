#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Naufalinux
# Build the Crawl4AI server image on top of the local Playwright image (no pull of the base image).
# Usage: deploy/build.sh [workdir]   (default: ./deploy/.build)
set -euo pipefail
C4AI_TAG="v0.9.5"; PW_VER="1.64.1"
HERE="$(cd "$(dirname "$0")" && pwd)"; WORK="${1:-$HERE/.build}"
podman image exists "mcr.microsoft.com/playwright:v${PW_VER}-noble" \
  || { echo "Missing local image mcr.microsoft.com/playwright:v${PW_VER}-noble (no pull attempted)" >&2; exit 1; }
mkdir -p "$WORK/src"
curl -fsSL "https://github.com/unclecode/crawl4ai/archive/refs/tags/${C4AI_TAG}.tar.gz" | tar -xz -C "$WORK/src" --strip-components=1
cp "$HERE/Dockerfile" "$HERE/.dockerignore" "$WORK/"
cd "$WORK"
podman build --pull=never --format docker --memory=2g -t "localhost/crawl4ai-pw:${C4AI_TAG#v}-pw${PW_VER}" -f Dockerfile .
