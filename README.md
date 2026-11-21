# hermes-crawl4ai-playwright-nfx

A small [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin that registers **Crawl4AI** as the
`web_extract` backend, plus a bounded same-site crawl tool and an **opt-in Firecrawl fallback** for pages Crawl4AI
reports as blocked by anti-bot protection. Search is not included; pair it with a search backend such as SearXNG.

- Calls `POST {CRAWL4AI_URL}/md` with `{"url": ..., "f": "fit"|"raw"}` and a Bearer token.
- One dependency on Hermes' public provider contract (`agent.web_search_provider`); `httpx` ships with Hermes.
- URLs to private networks are already blocked by Hermes before any backend is called.
- Errors are returned per URL and never include the token. A page blocked by anti-bot protection now says so
  (`Crawl4AI: Blocked by anti-bot protection: ... (HTTP 502)`) instead of an opaque `HTTP 502`.
- Settings are read only through `agent.secret_scope.get_secret_str` (profile-scoped; fails closed on a multiplexed gateway without a scope). The plugin never reads `.env` or `os.environ` itself.
- Tested against Hermes v0.23.1 and Crawl4AI v0.9.5 (see "Status").

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `CRAWL4AI_URL` | yes | `http(s)://host:port`, no credentials, query or fragment |
| `CRAWL4AI_API_TOKEN` | yes for Crawl4AI ≥ 0.9 | Put it in `~/.hermes/.env` (secret) |
| `CRAWL4AI_FILTER` | no | `fit` (default) or `raw` |
| `CRAWL4AI_TIMEOUT` | no | seconds per URL, 5-300, default 60 |
| `CRAWL4AI_MAX_CHARS` | no | truncate each page to N chars (2000-200000); unset or 0 = no truncation |
| `CRAWL4AI_CRAWL_TIMEOUT` | no | deadline for one `crawl4ai_crawl` call, 30-300 s, default 180 |
| `CRAWL4AI_FALLBACK` | no | `firecrawl` enables the fallback below; **off by default** |
| `FIRECRAWL_API_KEY` | only for the fallback | secret; the key alone does not enable anything |
| `CRAWL4AI_FALLBACK_MAX_PER_CALL` | no | fallback fetches per extract call, default 3 |
| `CRAWL4AI_FALLBACK_MAX_PER_DAY` | no | fallback fetches per process per day, default 20 (counter is in memory; resets on restart) |
| `CRAWL4AI_FALLBACK_DENY_DOMAINS` | no | comma-separated domains that are never sent to the fallback |

```bash
hermes config set web.search_backend searxng
hermes config set web.extract_backend crawl4ai
```

## Firecrawl fallback (opt-in)

When `CRAWL4AI_FALLBACK=firecrawl` and `FIRECRAWL_API_KEY` are both set, a URL whose Crawl4AI response says
*blocked by anti-bot protection* is retried once on Firecrawl Cloud (`POST https://api.firecrawl.dev/v2/scrape`,
1 credit per page). The endpoint is fixed in the code so the key can never be sent elsewhere.

- **Triggers only on anti-bot blocks.** Timeouts, an unreachable server, a wrong token and other HTTP errors never
  leave your machine.
- **Never sent:** URLs with a query string, fragment or credentials; domains in `CRAWL4AI_FALLBACK_DENY_DOMAINS`;
  URLs Hermes' URL-safety or website policy rejects. Only the URL is sent, never page content, prompts or history.
- **Budgets:** `CRAWL4AI_FALLBACK_MAX_PER_CALL` and `CRAWL4AI_FALLBACK_MAX_PER_DAY`. Over budget, the original
  Crawl4AI error is returned with `(fallback skipped: budget reached)`.
- **Provenance:** a fallback result starts with `> Fetched via Firecrawl Cloud ...` and carries
  `metadata.extracted_via = "firecrawl-cloud"`. A result under 1,500 characters is flagged (`metadata.thin`) because
  it may be an index page. A Firecrawl failure, empty page or challenge page is an error that still shows the
  original Crawl4AI reason. The log records the host only.
- **Cost and terms:** Firecrawl's pricing, quota and terms of service were not reviewed here. Firecrawl sees every
  fallback URL. Enable it per profile and not on profiles whose work topics should not leave your machine.

## Crawl tool

`crawl4ai_crawl` (toolset `crawl4ai`, enable it when needed) follows links breadth-first under the seed host and its
subdomains: `basic` = one link level, `advanced` = two, `max_pages` 1-20. Links are followed in the plugin with
single-page `/crawl` calls, so every URL is checked against Hermes' URL-safety and website policy before it is sent
to Crawl4AI, and the final URL of each result is checked again. A blocked page becomes a per-page error. The crawl
tool does **not** use the Firecrawl fallback. Adapted from `hermes-web-crawl4ai` (MIT); see `THIRD_PARTY_NOTICES.md`.

## Install

This plugin is **not in the Hermes plugin catalog**, so a bare-name install (`hermes plugins install <name>`) is
unavailable. Install it from GitHub, pinned to an exact commit (use the commit you have reviewed):

```bash
hermes plugins install naufalinux/hermes-crawl4ai-playwright-nfx --ref <40-character-commit-sha> --enable
```

Hermes prints an "unreviewed source" warning for non-catalog plugins; read the code (about 520 lines) and the
Disclosure section first. Then set `CRAWL4AI_URL` and `CRAWL4AI_API_TOKEN` in `~/.hermes/.env` and select the backend:

```bash
hermes config set web.extract_backend crawl4ai
```

Restart running Hermes sessions afterwards. For development, symlink this directory to
`~/.hermes/plugins/hermes-crawl4ai-playwright-nfx` and run `hermes plugins enable hermes-crawl4ai-playwright-nfx`.

After changing the plugin code, reload it in long-running Hermes processes with
`hermes plugins disable hermes-crawl4ai-playwright-nfx && hermes plugins enable hermes-crawl4ai-playwright-nfx`;
a process that already imported the old module keeps running the old code otherwise.

The separate community plugin [`hermes-web-crawl4ai`](https://github.com/axAilotl/hermes-web-crawl4ai) (backend id
`crawl4ai-bounded`) is the origin of the crawl code here; it does not have the Firecrawl fallback.

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

Verified (0.2.0): 61 unit tests (24 original, 27 for typed errors / fallback / budgets / limits, 10 for the crawl tool); five deliberate mutations (query-string guard removed, per-call cap ignored, fallback on any error, same-site check removed, API key echoed in an error) each made tests fail. `hermes plugins validate` passes with one warning: the plugin imports Hermes-internal helpers (`tools.url_safety`, `tools.website_policy`), which can break on a Hermes release (catalog rule 9). Live checks against Crawl4AI 0.9.5, Firecrawl Cloud and Hermes v0.23.1: two anti-bot-blocked pages (a Wiley article and an NIA article) were served by the fallback, a PMC page stayed local, a URL with a query string was not sent out, `crawl4ai_crawl` returned 4 same-site pages, and a `hermes chat` `web_extract` call returned the Wiley article with the provenance line. Earlier (0.1.0): 24 unit tests (fake Crawl4AI server plus the real `agent.secret_scope` for the credential route, including multiplex fail-closed and profile-isolation cases; a mutation check confirmed they fail if settings are read from `os.environ`); the provider class run directly against a live Crawl4AI
v0.9.5 container built from `deploy/` (markdown + title returned, non-http URL refused, wrong token gives a
clear 401 message). Also verified end-to-end: a live Hermes v0.23.1 `web_extract` call (`hermes chat -q`) fetched
`react.dev/reference/react/useState` through this plugin and Crawl4AI (container log shows the matching `/md` request).
The container needs ~10 s to stop (supervisord ignores SIGTERM until then); keep Quadlet's default stop timeout.

## Roadmap

Plan only: **nothing in this section is implemented yet** unless marked done. Items are ordered by value;
each release is a new pinned commit (catalog entries pin an exact SHA, so every release needs a SHA-bump PR).

### 0.2: bug fixes and hardening

- [x] **Opaque errors.** Done in 0.2.0: the server's `detail` is parsed (trimmed to 200 chars) and a block is
  reported as such and triggers the opt-in fallback. Still open: a machine-readable reason field
  (`blocked`, `timeout`, `auth`, `upstream`) instead of message text.
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

- **Network:** one outbound call per extracted URL, `POST {CRAWL4AI_URL}/md` (and `/crawl` for the crawl tool), to the
  Crawl4AI server *you* configure. It sends the target URL, the filter mode (`fit`/`raw`) and, if set, your bearer
  token. It has no default endpoint: with `CRAWL4AI_URL` unset it does nothing. **Only if you set
  `CRAWL4AI_FALLBACK=firecrawl` and `FIRECRAWL_API_KEY`** does it also call `https://api.firecrawl.dev/v2/scrape`
  for URLs Crawl4AI reported as anti-bot-blocked (URL and your Firecrawl key only; see "Firecrawl fallback").
- **Reads:** only the variables listed in the Configuration table, through `agent.secret_scope.get_secret_str`. It never reads `.env`, `auth.json`, other credential stores or any file.
- **Writes / processes:** none. No files written, no shell commands, no subprocesses, no background processes, no
  self-update, no telemetry or usage reporting. The daily fallback counter lives in memory only.
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
- `crawl.py` is adapted from the MIT-licensed `hermes-web-crawl4ai` (see `THIRD_PARTY_NOTICES.md`).
- Hermes Agent is MIT and `httpx` is BSD-3-Clause; both are GPL-compatible. The plugin is loaded into the Hermes
  process, so distributing a build that bundles Hermes and this plugin together must follow GPL-3.0 for the plugin part.
