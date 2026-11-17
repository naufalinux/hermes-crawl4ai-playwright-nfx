# hermes-crawl4ai-playwright-nfx

A small [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin that registers **Crawl4AI** as the
`web_extract` backend. It is extract-only; pair it with a search backend such as SearXNG.

- Calls `POST {CRAWL4AI_URL}/md` with `{"url": ..., "f": "fit"|"raw"}` and a Bearer token.
- One dependency on Hermes' public provider contract (`agent.web_search_provider`); `httpx` ships with Hermes.
- URLs to private networks are already blocked by Hermes before any backend is called.
- Errors are returned per URL and never include the token.
- Settings are read only through `agent.secret_scope.get_secret_str` (profile-scoped; fails closed on a multiplexed gateway without a scope). The plugin never reads `.env` or `os.environ` itself.
- Tested against Hermes v0.23.1 and Crawl4AI v0.9.5 (see "Status").

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `CRAWL4AI_URL` | yes | `http(s)://host:port`, no credentials, query or fragment |
| `CRAWL4AI_API_TOKEN` | yes for Crawl4AI ≥ 0.9 | Put it in `~/.hermes/.env` (secret) |
| `CRAWL4AI_FILTER` | no | `fit` (default) or `raw` |
| `CRAWL4AI_TIMEOUT` | no | seconds per URL, 5-300, default 60 |

```bash
hermes config set web.search_backend searxng
hermes config set web.extract_backend crawl4ai
```

## Install

Place (or symlink) this directory at `~/.hermes/plugins/hermes-crawl4ai-playwright-nfx` and enable it
through Hermes' plugin commands (`hermes plugins --help`). Restart running Hermes sessions afterwards.

## Server image (optional, `deploy/`)

The official `unclecode/crawl4ai` image is ~5.8 GB extracted. `deploy/Dockerfile` instead builds the same
server on top of the Playwright image you may already have (`mcr.microsoft.com/playwright:v1.64.1-noble`),
adding ~0.9 GB. Notes:

- Playwright is pinned to **1.64.1** to match the bundled browsers; update both together.
- `pip check` reports upstream pin conflicts (`anyio==4.9.0` vs `openai`/`httpx2`), identical to the
  official image; they only affect Crawl4AI's LLM features, not `/md`.
- `deploy/build.sh` never pulls the base image. `deploy/crawl4ai.container.example` is a loopback-only Quadlet.

## Development

```bash
python -m venv .venv && .venv/bin/pip install httpx pytest
PYTHONPATH=/path/to/hermes-agent .venv/bin/python -m pytest -c tests/pytest.ini tests
```

The tests use a stdlib fake Crawl4AI server; no network is needed.

## Status

Verified: 24 unit tests (fake Crawl4AI server plus the real `agent.secret_scope` for the credential route, including multiplex fail-closed and profile-isolation cases; a mutation check confirmed they fail if settings are read from `os.environ`); the provider class run directly against a live Crawl4AI
v0.9.5 container built from `deploy/` (markdown + title returned, non-http URL refused, wrong token gives a
clear 401 message). Also verified end-to-end: a live Hermes v0.23.1 `web_extract` call (`hermes chat -q`) fetched
`react.dev/reference/react/useState` through this plugin and Crawl4AI (container log shows the matching `/md` request).
The container needs ~10 s to stop (supervisord ignores SIGTERM until then); keep Quadlet's default stop timeout.

## Roadmap

Plan only: **nothing in this section is implemented yet** unless marked done. Items are ordered by value;
each release is a new pinned commit (catalog entries pin an exact SHA, so every release needs a SHA-bump PR).

### 0.2: bug fixes and hardening

- [ ] **Opaque errors.** A page blocked by anti-bot protection surfaces as `Crawl4AI returned HTTP 502`. Parse the
  server's JSON error body (verified: `{"detail": "Blocked by anti-bot protection: Structural: minimal_text, ..."}`)
  and return a typed reason (`blocked`, `timeout`, `auth`, `upstream`) so the agent can pick another source
  instead of guessing.
- [ ] **Transient failures.** Bounded retry (at most 2, exponential backoff with jitter, honoring `Retry-After`) for
  502/503/timeouts only; never retry 4xx. GitHub returned an intermittent 503 during testing.
- [ ] **Titles.** `/md` returns no title; when the markdown has no `# ` heading the title falls back to the URL.
  Use the `/crawl` metadata (see 0.3) or the first heading of any level.
- [ ] **Redirects and SSRF.** Test how a redirect from a public URL to a private address is handled by Hermes and by
  Crawl4AI 0.9.x (currently untested; disclosed above). Add a regression test with a local redirecting server.
- [ ] **Response guards.** Cap response size, check `Content-Type`, validate the JSON schema.
- [ ] **Contract test.** Replay a recorded real `/md` response so an upstream schema change fails CI, not users.
- [ ] **CI.** GitHub Actions running `pytest` and `hermes plugins validate --install-deps` against the minimum
  supported Hermes (`>=0.23.1`) and the latest release; actions pinned by SHA.
- [ ] **Bounded concurrency.** Extraction is sequential per call. Add `CRAWL4AI_CONCURRENCY` (default 1, matching the
  server's single worker).

### 0.3: improvements

- [ ] Optional `/crawl` backend: returns `metadata.title`, final URL after redirects and status code.
- [ ] `config_schema` for the non-secret URL (token stays a secret) and `requires_env` for install-time prompts.
- [ ] `hermes crawl4ai doctor` (CLI via `register_cli_command`): checks URL, token, `/health`, version and the
  Playwright pin. Diagnostics only, no deployment wizard.
- [ ] Optional `bm25` filter when the caller passes an objective (`q`), to cut tokens further.
- [ ] `deploy/`: base image pinned by digest, hash-pinned Python requirements (`pip --require-hashes`), a
  GitHub-Actions build with build-provenance attestation, and a rootless-userns / seccomp profile so Chromium can
  run **without** `--no-sandbox` (the upstream config suggests this path).
- [ ] Compatibility matrix (Hermes, Crawl4AI, Playwright) and an upgrade guide.

### 0.4: stealth and anti-bot handling

What Crawl4AI 0.9.5 allows (checked in its source and on the running server):

- `/md` accepts only `url`, `f`, `q`, `c`, `provider`, `temperature`: **no browser options**.
- `/crawl` accepts `browser_config` / `crawler_config`. An untrusted request body may set `enable_stealth`
  (playwright-stealth, documented as "basic bot detection" only), `user_agent`, viewport, `text_mode`,
  `check_robots_txt`, `max_retries`, `mean_delay`, `wait_until`, `page_timeout`, `locale`, `timezone_id`.
- It **rejects** (HTTP 400) `proxy_config`, `extra_args`, `magic`, `simulate_user`, `override_navigator`, `js_code`,
  `cdp_url` and `fallback_fetch_function` from a request. Those can only be set in the server's own config.
- Blocked pages are detected server-side ("Blocked by anti-bot protection: ...") and returned as HTTP 502.

Plan, in order; each phase ships only if it improves a **measured** result:

1. **Measure (no evasion).** Fixed list of ≥30 public pages, spaced requests, recording status and block reason.
   Establishes the baseline blocked rate. Without it, "stealth works" is a guess.
2. **Detect and report.** Classify blocks (HTTP 403/429/503, Cloudflare-style challenges, "minimal text" shells) into
   a distinct `blocked` error with a hint to try an official source. Add per-host rate limiting with jitter and a
   short cooldown after a block (rapid repeats are what triggered Hacker News), plus a small result cache.
3. **Honor the rules.** `check_robots_txt` as a setting; refuse hosts the user lists in `CRAWL4AI_DENY_HOSTS`.
4. **Prefer official sources.** Opt-in URL rewrite rules to official or raw endpoints (for example
   `github.com/<o>/<r>/blob/...` to `raw.githubusercontent.com`; CVE pages to the NVD REST API) before any evasion.
5. **Opt-in stealth, escalation only.** `CRAWL4AI_STEALTH=true`: after a *blocked* `/md`, retry once through `/crawl`
   with `enable_stealth` and a realistic `user_agent`/viewport. Default **off**. Server-side options (a non-text
   browser mode, evaluating the `patchright` dependency Crawl4AI already ships) are documented as operator choices
   in `deploy/`, with their memory cost measured first.
6. **Acceptance.** Blocked rate on the phase-1 list drops measurably with no regression in tests or latency
   budget; request payloads covered by unit tests; every new behavior added to the Disclosure section.

Guardrails: low-volume reading on behalf of one user, not crawling. No CAPTCHA solving, no residential or rotating
proxy services, no reuse of logged-in sessions, and no evasion against sites whose terms or `robots.txt` forbid
automated access. Any third-party service would be a new, separately disclosed opt-in. Stealth effectiveness is
**unmeasured today**; treat it as a hypothesis until phase 1 produces numbers.

## Disclosure

What a user should know before installing (Hermes plugin catalog rule 13):

**The plugin itself (`provider.py`, `__init__.py`, loaded by Hermes)**

- **Network:** one outbound call per extracted URL, `POST {CRAWL4AI_URL}/md`, to the Crawl4AI server *you* configure.
  It sends the target URL, the filter mode (`fit`/`raw`) and, if set, your bearer token. It contacts no other host
  and has no default endpoint: with `CRAWL4AI_URL` unset it does nothing.
- **Reads:** only `CRAWL4AI_URL`, `CRAWL4AI_API_TOKEN`, `CRAWL4AI_FILTER` and `CRAWL4AI_TIMEOUT`, through
  `agent.secret_scope.get_secret_str`. It never reads `.env`, `auth.json`, other credential stores or any file.
- **Writes / processes:** none. No files written, no shell commands, no subprocesses, no threads or background
  processes, no self-update, no telemetry or usage reporting.
- **Credentials:** the plugin stores nothing; Hermes holds the token in its own credential store.
- **Untrusted content:** the markdown returned by Crawl4AI is passed to the agent unchanged. It comes from arbitrary
  web pages, so treat it as data, not instructions (prompt injection).

**The Crawl4AI server you point it at**

- It fetches the target pages with a headless Chromium, so the URL (and your server's IP) reach the target site.
  Hermes blocks private-network URLs before any backend runs, and Crawl4AI 0.9.x adds its own egress guard, but
  how redirects from a public URL to an internal address are handled has not been tested here. Keep the server on a
  network that cannot reach sensitive services.
- The reference image (see below) runs Chromium with `--no-sandbox`: the upstream server config (`config.yml`)
  sets it for non-root containers, which weakens browser isolation. Use it only for public web extraction.

**Optional `deploy/` helpers (not loaded by Hermes, run by you)**

- `deploy/build.sh` runs `podman build` and downloads the Crawl4AI source for tag `v0.9.5` from
  `github.com/unclecode/crawl4ai` with `curl`. The `Dockerfile` installs packages from the Ubuntu apt repositories
  and PyPI at build time (versions of Playwright and Crawl4AI pinned; transitive dependencies are not hash-pinned).
  It never pulls the base image. Nothing here runs unless you invoke it.
- `deploy/crawl4ai.container.example` is a loopback-only Quadlet unit you copy yourself.

**Related Hermes behavior (not the plugin's)**

- With Hermes' default `web.keyless_rescue: true`, a call that fails on *every* URL is retried once on a free
  third-party service, which sends those URLs outside your machine and hides the failure from the agent. To keep
  extraction strictly self-hosted, set `hermes config set web.keyless_rescue false`.

## License

GPL-3.0-only. Copyright (C) 2026 Naufalinux. See `LICENSE`.

- Crawl4AI is Apache-2.0 (compatible with GPL-3.0) and is not redistributed here; `deploy/build.sh` downloads it.
- Hermes Agent is MIT and `httpx` is BSD-3-Clause; both are GPL-compatible. The plugin is loaded into the Hermes
  process, so distributing a build that bundles Hermes and this plugin together must follow GPL-3.0 for the plugin part.
