# Releasi — LinkedIn Automation Tool

## Server
- **IP**: 89.167.80.102
- **SSH**: `ssh root@89.167.80.102`
- **Docker container**: `releasi`
- **Live DB**: `/app/data/releasi.db` inside container
- **Query DB**: `docker exec releasi python3 -c "import sqlite3; ..."` (no sqlite3 binary in container)
- **Claude can always SSH and restart the server autonomously** — no need to ask for permission. If diagnosing an issue requires a restart (stuck pool, hung process, post-deploy), just do it: `ssh root@89.167.80.102 'docker restart releasi'`

## Critical Rules (AI assistant must follow)
- **Never activate or resume a campaign** unless the user explicitly asks. Campaigns may be paused intentionally. Activating them uninvited can fire connection requests the user hasn't approved.
- **Never reset lead statuses** (ERROR → PENDING, SCHEDULED → PENDING, etc.) unless the user explicitly asks.
- **Never send bare HTTP requests with `li_at`** — always use a full browser context (see Testing & Diagnostics below).

## Deploying code changes
`src/` is volume-mounted from `/root/linauto/src` — but **Python caches imported modules in `sys.modules`**. A `git pull` updates files on disk but the running process keeps old code. **Always restart the container after pulling**:

```bash
ssh root@<your-server-ip>
cd /root/linauto && git pull && docker restart releasi
```

Or use the deploy script: `ssh root@<your-server-ip> 'cd /root/linauto && bash scripts/deploy.sh'`

The deploy script automatically pauses active accounts before the restart and resumes exactly those accounts after the container is healthy. This prevents missed dispatches mid-deploy.

Only rebuild the image when changing **dependencies** (`pyproject.toml`) or **`config.py`/`models.py`** (pydantic/SQLAlchemy schema changes). `docker-compose build` is broken (v1.29 incompatibility) — use `docker run` directly:

**Secrets are stored in `/root/linauto/.env`** (not inline in the command — keeps them out of `ps aux`). Create/update it once:
```bash
cat > /root/linauto/.env << 'EOF'
RELEASI_API_KEY=<your-api-key>
RELEASI_TELEGRAM_API_ID=<api_id>
RELEASI_TELEGRAM_API_HASH=<api_hash>
RELEASI_TELEGRAM_SESSION=<telethon_string_session>
EOF
chmod 600 /root/linauto/.env
```

Telegram credentials are used by `telegram/resolver.py` for per-lead username lookup. They map to `config.telegram_api_id`, `config.telegram_api_hash`, `config.telegram_session` via Pydantic settings (prefix `RELEASI_`). If unset, the Find Telegram button on the lead detail page will error.

```bash
cd /root/linauto && git pull
docker-compose build
docker stop releasi && docker rm releasi
docker run -d --name releasi --restart unless-stopped \
  -v /root/linauto/data:/app/data \
  -v /root/linauto/config/settings.yaml:/app/config/settings.yaml:ro \
  -v /root/linauto/src:/app/src \
  -p 8000:8000 -p 6080:6080 \
  --env-file /root/linauto/.env \
  -e RELEASI_API_ENABLED=true \
  -e "RELEASI_CORS_ORIGINS=[]" \
  -e RELEASI_LOG_LEVEL=INFO -e TZ=Europe/Berlin \
  --memory=3g --cpus=1.5 \
  releasi_releasi:latest
```

## Dashboard Deployment (Vercel)

The dashboard (`dashboard/`) is deployed on Vercel and **is git-connected** — pushing to `master` on GitHub triggers an automatic Vercel deployment. No manual `npx vercel deploy` needed.

- To deploy frontend changes: `git push` (Vercel picks it up automatically)
- Vercel project is linked to the `NicolasGohler/releasi` GitHub repo, `master` branch

## Dashboard → API security model

The dashboard (Vercel, Next.js) and backend API (FastAPI on `<your-server-ip>:8000`) are gated as follows. **Do not regress any of this.**

1. **Server-side key injection.** The dashboard never ships the API key to the browser. Browser calls go to same-origin `/api/v1/*`, which is handled by the Next.js route at `dashboard/src/app/api/v1/[...path]/route.ts`. That route forwards to `BACKEND_URL`, injects `Authorization: Bearer ${BACKEND_API_KEY}` server-side, and streams request/response bodies (CSV upload + export both depend on this).
   - **Vercel env vars**: `BACKEND_URL` (e.g. `http://<your-server-ip>:8000`) and `BACKEND_API_KEY` (matches `RELEASI_API_KEY` on the server). Both are **server-only** — do NOT prefix with `NEXT_PUBLIC_`.
   - **Never add `NEXT_PUBLIC_API_KEY`** back to `dashboard/.env*` or `lib/api.ts`. That was the old model and leaked the key to every visitor.
   - **Never add a `/api/v1/:path*` rewrite back to `next.config.ts`**. Rewrites bypass the route handler, so the key injection would be skipped.

2. **CORS fails closed.** `RELEASI_CORS_ORIGINS=[]` in the container env (see deploy block above). The Next proxy is same-origin, so the browser never needs cross-origin access. If you add another dashboard domain, add it explicitly — do not restore `["*"]`.

3. **Rate limiting.** `slowapi` is wired in `src/releasi/api/app.py` at 120 req/min per client IP (X-Forwarded-For aware — the Next proxy forwards the real client IP). Scheduler jobs run in-process and do NOT hit HTTP, so they bypass the limit. If you see 429s in dashboard usage, raise `default_limits` in `app.py` rather than disabling the middleware.

4. **Vercel Deployment Protection** (manual toggle on Vercel project → Settings → Deployment Protection) is the outer gate. Without it, anyone with the URL reaches the Next.js app — which can't leak the key directly, but can still drive the proxy. Keep it enabled.

5. **Per-user login layer** (`dashboard/src/middleware.ts` + `users` table, migration 032). Every dashboard request must carry a signed `releasi_session` cookie (HMAC-signed with `DASHBOARD_SECRET`, carrying the user's UUID, **1-week** expiry). Login is **handle + password per user** validated against the backend `users` table (bcrypt hashes) via `POST /api/v1/auth/login` — there is **no shared `DASHBOARD_PASSWORD`** anymore (that was the old single-password model; do not reintroduce it). Required Vercel env var: `DASHBOARD_SECRET` (server-only). If unset, the gate is disabled (safe for local dev). To force logout everywhere, rotate `DASHBOARD_SECRET`. Provision users on the server: `releasi user add --handle <name> [--superadmin] --password <pw>`.

6. **User-identity forwarding for attribution.** The Next proxy verifies the signed session cookie **server-side** and forwards the resolved user id to the backend as `X-Releasi-User-Id` (it strips any inbound copy first, so the browser can't spoof it). The backend `get_current_user_id` dependency (`api/deps.py`) reads that header; API-key-only callers and the scheduler don't set it → `None` → attributed to "System". This is how manual actions get stamped — see "Manual-Action Attribution" below. Do **not** trust a client-supplied `X-Releasi-User-Id`.

7. **Adding a new API call in `lib/api.ts`**: use the existing `apiFetch` helper or fetch to `/api/v1/...` (same-origin). Never build absolute URLs to `<your-server-ip>:8000` — that bypasses the proxy and would require re-exposing the key.

## Testing & Diagnostics on LinkedIn (CRITICAL)

When running any diagnostic script, test, or one-off action against LinkedIn:

1. **Always use the account's proxy** — never hit LinkedIn from the server IP. Use `LinkedInBrowser` (which configures the proxy automatically) or manually pass the proxy. A request from a Helsinki server IP when the account normally browses from a US residential IP is a strong suspension signal.

2. **One browser context at a time** — never open multiple simultaneous browser contexts for the same account. LinkedIn sees concurrent sessions from different IPs (or even the same IP) as credential sharing and may invalidate the cookie. Close each context before opening the next.

3. **Reuse the account's persistent browser profile** — use `LinkedInBrowser.launch()` with the account's stored credentials, not raw Playwright. This preserves cookies, localStorage, and fingerprint consistency.

4. **No rapid-fire page loads** — add delays (`asyncio.sleep(2-3)`) between navigations. Multiple pages loaded in <1 second looks like scraping.

5. **Always close the browser** — use `try/finally` to ensure `browser.close()` is called. Abandoned browser processes hold the cookie and may cause conflicts with the BrowserPool.

6. **Prefer `--dry-run`** — diagnostic scripts should default to read-only observation (screenshots, DOM inspection) and only perform actions (clicking Send, etc.) when explicitly opted in.

7. **Never send bare HTTP requests with `li_at`** — always use a full browser context. LinkedIn treats bare cookie requests from non-browser user agents as stolen-cookie tests and invalidates the session.

8. **Run diagnostics inside the container** — `docker exec releasi python3 /app/scripts/diag_connect.py`. This ensures the correct Python environment, access to browser profiles, and proper proxy routing.

## Overview
Dripify alternative. Automates LinkedIn connection requests and follow-up messages with safety-first design (warmup ramps, cooldowns, rate limits, stealth browsing).

## Tech Stack
- **Python 3.11 image / 3.12 host**: use `Optional[X]` not `X | None` in SQLAlchemy `Mapped[]` annotations — `X | None` syntax breaks on the container's pydantic_core build.
- FastAPI + SQLAlchemy 2.0 async (aiosqlite) + APScheduler (7 jobs) + Playwright + Telethon.

## Project Structure
```
fundraising/
├── fundraising.py          # Weekly lead-gen pipeline (runs on HOST via systemd, not in Docker)
└── requirements.txt        # Host venv deps (pyyaml, playwright, bs4, pandas, slack-sdk)

src/releasi/
├── cli.py                  # Typer CLI (all commands)
├── config.py               # Pydantic settings from YAML
├── db/
│   ├── engine.py           # Async SQLAlchemy engine (NullPool)
│   ├── models.py           # ORM models (incl. scraper_cookies, lead_events)
│   └── repository.py       # Data access layer
├── campaign/
│   ├── executor.py         # Orchestrates browser actions + DB updates
│   ├── importer.py         # CSV import with auto-detect LinkedIn URLs
│   ├── state_machine.py    # Lead status transitions
│   └── template.py         # {{variable}} rendering
├── linkedin/
│   ├── actions.py          # Atomic actions (connect, message, withdraw)
│   ├── browser.py          # Playwright browser lifecycle + fingerprinting
│   ├── detector.py         # Limit/CAPTCHA/session detection
│   ├── login_session.py    # noVNC-based manual login flow (ephemeral Xvfb)
│   ├── navigator.py        # Page navigation
│   ├── noise.py            # Feed scroll, likes, profile views (human-like)
│   ├── pool.py             # Persistent BrowserPool singleton (max 3 contexts)
│   ├── profile_filter.py   # Live profile quality filtering
│   └── selectors.py        # ALL LinkedIn DOM selectors (update here when LinkedIn changes)
├── safety/
│   ├── cooldown.py             # Smart Monday-retry cooldown
│   ├── delays.py               # Human-like delays + typing simulation
│   ├── dispatch_decisions.py   # Pure result classifiers → DispatchIntent (no side effects)
│   ├── error_signals.py        # Error signal extraction helpers
│   └── limits.py               # Rate tracking
├── scheduler/
│   ├── planner.py          # Clustered daily plan generation
│   └── runner.py           # APScheduler daemon (7 jobs) + warmup, keep-alive logic
├── telegram/
│   └── resolver.py         # Async Telethon-based Telegram username finder
└── api/
    ├── app.py              # FastAPI app factory
    ├── auth.py             # API key auth
    ├── schemas.py          # Pydantic request/response models
    └── routes/
        ├── accounts.py     # Account CRUD + login-session endpoints
        ├── campaigns.py    # Campaign management
        ├── leads.py        # Lead management + find-telegram background tasks
        ├── lead_lists.py   # Lead list endpoints
        ├── scrapers.py     # Scraper cookie refresh (noVNC) + cookie fetch endpoints
        ├── stats.py        # Stats endpoints
        └── health.py       # Health check
```

## Key Files
- `src/releasi/linkedin/selectors.py` — ALL LinkedIn DOM selectors. Update here when LinkedIn changes their UI.
- `src/releasi/linkedin/browser.py` — Browser fingerprinting. `_CHROMIUM_MAJOR` must match the actual Playwright Chromium binary (check with `chrome --version` in container).
- `src/releasi/db/models.py` — SQLAlchemy models. Use `Optional[X]` (not `X | None`) for Mapped[] annotations.
- `config/settings.yaml.example` — Reference config with all available settings.
- `src/releasi/campaign/importer.py` — CSV import. Handles `name`/`full_name` columns (splits into first/last) and `project`/`project_name` columns (maps to company). Add new column aliases to `_COLUMN_MAP` or `_FULL_NAME_COLUMNS` here.
- `src/releasi/telegram/resolver.py` — Async Telethon Telegram username finder. Two-pass: (1) checks Twitter handle on Telegram, (2) scores name+company pattern candidates. Returns `FindResult(best_match, alternatives, logs)`. Credentials from `config.telegram_api_id/hash/session`.

## Scheduler Jobs (7 total)
All times are **in the account's configured timezone** (e.g. `America/New_York` for Montreal). Jobs that need per-account timing fire hourly and skip accounts that are outside their window or have already run today.

| Job | Schedule | Purpose |
|-----|----------|---------|
| `daily_planner` | Hourly; plans once per local day (checked via `plan_generated_today()`) | Assign scheduled_at to pending leads |
| `dispatcher` | Every 5 min ±75s jitter | Execute due connection requests |
| `acceptance_checker` | Hourly; fires at `acceptance_check_hour` local time (default 10 AM) | Detect accepted connections via connections page scroll |
| `cooldown_checker` | 04:00 UTC daily | Resume paused accounts |
| `followup_dispatcher` | Every 30 min | Send follow-up messages |
| `keepalive` | Hourly; fires once in 7–10 AM local window | Organic morning LinkedIn session |
| `withdraw_invitations_sweep` | Hourly | Auto-withdraw oldest invitations when pending count exceeds threshold (disabled if `withdraw_threshold` is NULL) |

## Anti-Detection Layer

### Browser Fingerprinting (`browser.py`)
- **UA must match Chromium binary**: `_CHROMIUM_MAJOR` in `browser.py` must match the actual Playwright Chromium binary — a mismatch between `navigator.userAgent` and `navigator.userAgentData` is a strong bot signal. Check: `docker exec releasi /home/appuser/.cache/ms-playwright/chromium-*/chrome-linux64/chrome --version`
- **Timezone derived from proxy country**: The browser timezone is always set to match the proxy IP's country, not the account's `timezone` field. This prevents the fingerprint mismatch where the IP geolocates to Germany but `navigator.timezone` reports America/New_York. A warning is logged if they differ.
- **Residential proxy**: IPRoyal sticky sessions per account. Session ID rotates weekly so a dead IP self-heals within a week rather than persisting indefinitely.
- Other signals: country-aware UA pool (Mac for `{us, ca, gb, au, nz, ie}`, Windows elsewhere), deterministic viewport per account, playwright-stealth, `AutomationControlled` suppressed.

### Profile Filters (`profile_filter.py`)
Per-campaign filters checked live on the profile page before a connection request is sent. All filters **fail open** — if detection is uncertain (LinkedIn DOM change, restricted profile), the lead is not blocked.

| Filter | Campaign field | Skip reason logged |
|--------|---------------|--------------------|
| No profile photo | `filter_no_photo` | `filter_no_photo` |
| Fewer than N connections | `filter_min_connections` | `filter_low_connections:<count>` |
| "Open to Work" badge | `filter_exclude_open_to_work` | `filter_open_to_work` |

All three filters are toggleable per campaign from the dashboard Settings tab.

### Already-Connected Detection (`actions.py` → `send_connection_request`)
Multi-layer detection catches 1st-degree connections before the connect flow is attempted (JS scan, CSS selectors, More dropdown guard, dialog fallback).

**Critical**: `_find_dropdown_item_by_js("Connect")` uses **word-boundary matching** (exact/starts-with/ends-with), NOT substring `.includes()`. Substring matching caused "Remove connection" to match "connect" and be clicked instead, opening a removal confirmation dialog rather than an invite modal.

### Session Health Model
**Reactive-first** — LinkedIn sessions last months when left alone. Do not ping proactively.

| Trigger | Action |
|---------|--------|
| Scheduler startup | `check_cookie_health()` once — catches pre-deploy expiry |
| Login "Save" clicked | Background `check_cookie_health()` — confirms proxy route healthy |
| Pre-dispatch browser feed ping fails (redirect) | Try `_attempt_li_rm_reauth` first → if li_rm valid, recover to `active` silently; only if li_rm also dead → mark `cookie_expired` |
| Pre-dispatch browser feed ping times out | Skip cycle (proxy/network issue), do NOT mark `cookie_expired` |
| 3 consecutive session errors (non-network) | Mark `cookie_expired` as last resort |
| 3 consecutive network/timeout errors | Log `proxy_connectivity_issues`, skip cycle, do NOT mark `cookie_expired` |
| Morning warm-up detects login redirect | Mark `cookie_expired` |
| Acceptance checker connections page → login redirect | Try `_attempt_li_rm_reauth` first → if li_rm valid, recover to `active` silently; only if li_rm also dead → mark `cookie_expired` |
| `check_cookie_health` passes for expired account | No-op — `check_cookie_health` only queries ACTIVE accounts and never reactivates; recovery to `active` happens via li_rm reauth (above) or manual noVNC re-login |

**What NOT to do**: Never send bare HTTP requests with only `li_at` from the server IP. LinkedIn treats this as a stolen-cookie test and invalidates the session.

**Failure mode separation**: Navigation timeouts and proxy errors are tracked separately from session errors (`result["network_error"]=True`). Only session errors count toward cookie expiry detection — network errors skip the cycle without marking the account expired.

### Dispatcher Architecture (`runner.py` + `safety/dispatch_decisions.py`)
Two-layer design keeps session-health logic testable and prevents silent drift between dispatchers:

1. **Pure classifiers** (`safety/dispatch_decisions.py`) — `classify_connection_result()` and `classify_followup_result()` return a `DispatchIntent` dataclass (lead action, account action, Slack message, log event, stop flag). No side effects — fully unit-testable.
2. **Side-effect helpers** (`runner.py`) — `_apply_connection_intent()` and `_apply_followup_intent()` execute the DB writes, Slack pings, and structured logging.

**Key invariant**: never inline session-health decision logic in a dispatcher. Add a new case to the classifier instead, keep the dispatcher thin.

### Acceptance Checker (`runner.py` → `check_acceptances`)
- Runs once daily at `acceptance_check_hour` (default 10 AM local time).
- Loads `linkedin.com/mynetwork/connections/`, infinite-scrolls until the age cutoff (default 30h), diffs scraped slugs against `CONNECTION_REQUESTED` leads → marks matches as `CONNECTED`. No individual profile visits needed.
- Does **not** detect declines — withdrawn/declined invitations are intentionally ignored.
- Manual run: `releasi check-acceptances --account "Name" [--cutoff-hours 96] [--dry-run]`
- Much cheaper than the old invitation manager approach (~240 MB/day saved).

### Daily Planner (`runner.py` → `daily_planning_sweep`)
- Uses `repo.plan_generated_today(campaign_id, local_date)` to guard against re-planning on mid-day restarts.
- **Do NOT use `future_scheduled > 0`** as the "already planned" guard. The dispatcher backfill always keeps 1 lead in `SCHEDULED` state, so `future_scheduled` is almost always ≥ 1 and would permanently block the planner after a mid-day container restart.
- On mid-day restart: spreads remaining slots across the rest of the window. Skips planning if past `work_end_hour`.

### Morning Warm-Up (`runner.py` → `keep_alive`)
- Runs once per local day in the 7–10 AM window: feed scroll followed by one additional page (notifications, network, or messaging). Mimics a real morning LinkedIn session to maintain account health.
- Session expiry detected via URL redirect check, not an HTTP pre-check.

### Proxy Health Check (`_http_check_session`)
- Used only on startup + post-login, never on a recurring schedule — pinging sessions proactively shortens their lifespan.
- Confirms cookie validity via `httpx` through the account's residential proxy, but does NOT guarantee Playwright browser navigation will succeed. A passing HTTP check is a necessary but not sufficient condition.

### Navigation Timeouts (`navigator.py`)
- All `page.goto()` calls use `wait_until="domcontentloaded"` rather than `"load"` — `domcontentloaded` fires immediately on a `/login` redirect, so an expired session is detected in <2s rather than waiting out a 30s load timeout.

### Playwright Proxy Credentials
- **Always use separate `username`/`password` fields** — Playwright/Chromium silently ignores credentials embedded in the server URL string (`http://user:pass@host:port`). The browser code in `browser.py` parses the URL and splits them out; do not revert this.
- `httpx` (health checks) handles embedded URL credentials fine — only Playwright needs the split.

### Proxy Sticky Sessions (`_build_proxy_url`)
- Session ID rotates every Monday — prevents being permanently stuck on a dead/slow residential IP; worst case is one bad week.
- Do not remove the week component. The old fixed hash caused Italy proxy failures for 10+ days with no self-healing.

### Proxy Location Notes
- **Avoid city-level specificity** — use `ca` not `ca-montreal`. City filtering shrinks the IP pool and IPRoyal often returns 502 when no city IP is available.
- **Avoid Greece (`gr`)**: residential IPs there are too slow for browser-grade traffic. Use `it`, `de`, `nl`, `fr`, or `es` for Southern/Central European accounts.
- **Mac UA markets**: `{us, ca, gb, au, nz, ie}` — all others get Windows UA. Account for this when choosing proxy country.

## noVNC Login Flow
- `POST /api/v1/accounts/{id}/login-session` → starts ephemeral Xvfb + x11vnc + websockify on port 6080.
- `POST /api/v1/accounts/{id}/browse-session` → same stack but injects the stored `li_at` cookie so the user lands on the feed (no cookies saved on close).
- `GET /api/v1/accounts/{id}/browse-session/status` → returns `{"active": bool}` — dashboard checks this on mount to restore button state after reload.
- Dashboard opens noVNC via `http://SERVER_IP:6080/vnc.html?path=websockify%3Ftoken%3D{token}&autoconnect=true&resize=scale&quality=3&compression=9`.
- **websockify token format**: `--token-plugin TokenFile --token-source <file>` where the file contains `<token>: localhost:5999`. Do NOT pass a directory as `--token-source` (causes "Syntax error on line 1"). Do NOT put the token in the URL path segment (causes "Token not present"); it must be a `?token=` query param on the WebSocket upgrade.
- `POST /api/v1/accounts/{id}/login-session/finish` → extracts cookies, copies browser profile, evicts stale pool slot, triggers background health check.
- noVNC is **ephemeral** — torn down after finish or close. "Disconnection" after saving is expected.
- `_cleanup()` uses `asyncio.wait_for(timeout=5)` around Playwright close calls so a frozen browser can't block the Close Session button indefinitely.

## Running
```bash
# Install
pip install -e ".[dev]"

# CLI
releasi account add --name nicolas --li-at <cookie>
releasi campaign create --name test --account nicolas
releasi campaign import --campaign test --csv leads.csv
releasi execute-once --campaign test --limit 3
releasi run  # Start scheduler daemon
```

## Testing
```bash
pytest                    # Run all tests
pytest tests/unit/        # Unit tests only
pytest -x                 # Stop on first failure
```
Note: pydantic-dependent tests (cooldown, planner, warmup) fail locally on ARM Mac due to x86 pydantic_core mismatch. Run in Docker for full suite.

**pytest is NOT installed in the container** — the `releasi` Docker image uses the production `pip install -e .` (not `.[dev]`). To run tests inside the container you'd need to `pip install pytest` first. Unit tests for pure functions (like `safety/dispatch_decisions.py`) can be run locally with a `pip install -e ".[dev]"` virtualenv.

## Lead Social Fields & Outreach Tracking

The `leads` table has social/outreach columns enriched outside the main LinkedIn automation flow:

| Column | Type | Purpose |
|--------|------|---------|
| `twitter_url` | `String` | X/Twitter profile URL (normalized to `https://x.com/...`) |
| `telegram_username` | `String` | Telegram handle (without `@`) |
| `telegram_alternatives` | `JSON` | All candidate handles from the resolver (best-first). Cleared when user saves a definitive `telegram_username`. |
| `tg_contacted_at` | `DateTime` | UTC timestamp set when user marks "contacted via Telegram"; NULL = not yet contacted |
| `email` | `String` | Email from CSV import |

**Backfill note**: older CSVs stored social data in `extra_data` rather than the typed columns. If new imports contain social data in `extra_data`, run a backfill script targeting the relevant keys (e.g. `extra_data["Twitter Url"]` → `twitter_url`).

## lead_events Table (migration 023)

Lightweight event log for lead-level actions that don't require an `account_id` (unlike `action_log`). Used by the lead detail activity feed alongside `action_log` rows.

Current `event_type` values: `telegram_found`, `telegram_saved`, `telegram_removed`, `tg_contacted`, `tg_contacted_cleared`, `phone_enriched`, `tg_sweep_searched`.

Add via `repo.log_lead_event(lead_id, event_type, details_dict, actor_user_id=...)`. Pass `actor_user_id` for human-driven actions (None = automated/system). See "Manual-Action Attribution".

## Manual-Action Attribution (migration 034)

Human-driven actions are attributed to the logged-in dashboard user. `lead_events` and `lead_notes` both have an `actor_user_id` column (nullable FK to `users.id`; NULL = automated/system, e.g. the TG enrichment sweep or scheduler, or legacy pre-2026-06 rows that can't be backfilled).

**Flow:** dashboard login (handle+password) → signed `releasi_session` cookie carrying the user UUID → Next proxy verifies it server-side and forwards `X-Releasi-User-Id` → backend `get_current_user_id` dep (`api/deps.py`) → passed into `log_lead_event` / `add_lead_note`. Routes that stamp the actor: `PATCH /leads/{id}` (tg_contacted/cleared, telegram_saved/removed) and `POST /leads/{id}/notes`.

**Activity feed** (`GET /api/v1/activity`): resolves `actor_user_id` → display name (`actor_name`), shown as a "by <name>" chip. Filter to one person with `?user_id=<id>` (this excludes `action_log`, which has no human actor). The dashboard Activity page has a **Manual** group chip (notes + tg_contacted/cleared + telegram_saved/removed) and a person dropdown (`GET /api/v1/users`). Manual events (lead_events + notes) are **unscoped** — they carry no account/campaign — so the feed excludes them when an `account_id`/`campaign_id` filter is active (otherwise they leak into every account's view).

## Find Telegram (per-lead background task)

**Endpoints** (in `api/routes/leads.py`):
- `POST /leads/{lead_id}/find-telegram` — starts background `asyncio.create_task()`, returns `{task_id}` immediately
- `GET /leads/{lead_id}/find-telegram/{task_id}` — polls status; `status` is `"running" | "done" | "error"`

**In-memory task store**: tasks live in memory only — lost on container restart, client must retry.

**Session factory pattern**: background task creates its own DB session via `get_session_factory()()` (not the request-scoped session). This is the correct pattern for tasks that outlive the HTTP request.

**Re-search guard**: if a previous search completed with no match and no `telegram_username` is saved, the endpoint returns **409**. Pass `?force=true` to bypass. The dashboard shows a greyed-out "No match / retry" state instead of the Find button.

**Two-pass resolver** (`telegram/resolver.py`):
1. Pass 1: checks if the lead's Twitter handle exists on Telegram (fast, one lookup)
2. Pass 2: generates name + company shorthand candidates, scores by name similarity, returns best match + all alternatives

On completion, `telegram_alternatives` is persisted to the lead row and a `telegram_found` event is logged. When the user clicks a candidate chip or saves a username via PATCH, `telegram_alternatives` is cleared and `telegram_saved` is logged.

## Global Leads Filters

`GET /api/v1/leads` accepts these social filter query params in addition to the standard ones:

| Param | Type | Behaviour |
|-------|------|-----------|
| `has_telegram` | `bool` | `true` = `telegram_username IS NOT NULL`, `false` = IS NULL |
| `has_twitter` | `bool` | `true` = `twitter_url IS NOT NULL`, `false` = IS NULL |
| `has_email` | `bool` | `true` = `email IS NOT NULL`, `false` = IS NULL |
| `tg_contacted` | `bool` | `true` = `tg_contacted_at IS NOT NULL`, `false` = IS NULL |

All implemented in `repository.py → list_leads_global()`. The dashboard renders these as toggle chips above the lead table.

## Database Migrations
```bash
alembic upgrade head      # Apply all migrations
alembic downgrade -1      # Rollback one migration
```
Migration files are in `alembic/versions/`. Follow the existing naming pattern (e.g., `003_add_campaign_filters.py`).

**Critical**: `alembic/` is NOT volume-mounted — only `src/` and `data/` are. After a code-only deploy (`git pull` + container restart without rebuilding the image), `alembic upgrade head` inside the container uses the **baked-in** migration files from the old image and won't see new migration files from the pull. Two options:
1. **Rebuild the image** (`docker-compose build` → `docker run ...`) — picks up the new migration files.
2. **Apply the SQL directly + stamp alembic manually**:
   ```bash
   # Apply the schema change
   docker exec releasi python3 -c "
   import sqlite3; c = sqlite3.connect('/app/data/releasi.db')
   c.execute('ALTER TABLE ... ADD COLUMN ...')
   c.commit(); c.close()
   "
   # Tell alembic the new revision is applied
   # NOTE: alembic_version has a UNIQUE constraint — use DELETE + INSERT, not INSERT OR REPLACE
   docker exec releasi python3 -c "
   import sqlite3; c = sqlite3.connect('/app/data/releasi.db')
   c.execute('DELETE FROM alembic_version')
   c.execute(\"INSERT INTO alembic_version VALUES ('023_your_revision')\")
   c.commit(); c.close()
   "
   ```
Option 2 is fine for simple `ADD COLUMN` migrations. Use option 1 for complex migrations (data transforms, drops, renames).

## Conventions
- **Python 3.9 compat**: `from __future__ import annotations` for function signatures, but use `Optional[X]` (not `X | None`) in SQLAlchemy `Mapped[]` type annotations.
- **NullPool**: Using `sqlalchemy.pool.NullPool` to avoid GC warnings in CLI context.
- **Deterministic seeds**: `hashlib.sha256(f"{account_id}-{date}")` for reproducible daily variation.
- **Fail open**: Safety checks (profile filters, noise) should fail open — don't block actions if detection is uncertain.
- **Selector resilience**: Each selector has primary + fallback entries. Try all before giving up.
- **Pool vs ephemeral**: Scheduler jobs use the persistent `BrowserPool`. `execute-once` CLI uses ephemeral browsers.
- **Fingerprint stability**: UA, viewport, proxy session ID are all hash-derived from `account_id` — same account always presents identical fingerprint across restarts.

## Auto-Withdraw (currently disabled)
The auto-withdraw feature is **fully implemented but disabled**. It withdraws the oldest pending invitations when the pending count exceeds a threshold. To enable/disable for an account:

```bash
# Enable (e.g. withdraw oldest when pending > 300)
docker exec releasi python3 -c "
import sqlite3; c=sqlite3.connect('/app/data/releasi.db')
c.execute(\"UPDATE accounts SET withdraw_threshold=300 WHERE name='Nicolas Goehler'\")
c.commit(); c.close()
"

# Disable (set back to NULL)
docker exec releasi python3 -c "
import sqlite3; c=sqlite3.connect('/app/data/releasi.db')
c.execute(\"UPDATE accounts SET withdraw_threshold=NULL WHERE name='Nicolas Goehler'\")
c.commit(); c.close()
"
```

Code is in `runner.py` → `check_acceptances()`, `actions.py` → `withdraw_oldest_invitations()`.

## On-Demand Withdrawal (dashboard UI)
A manual withdrawal panel lives in the account Settings tab. It lets you:
1. **Check count** — navigates to `linkedin.com/mynetwork/invitation-manager/sent/` via browser+proxy and reads the "People (N)" filter pill.
2. **Withdraw N oldest/newest** — runs `actions.withdraw_invitations(count, order)` as a background task; polls `/accounts/{id}/invitations/withdraw/{task_id}` every 3s; marks matched leads `WITHDRAWN` in the DB.
- API endpoints: `POST /accounts/{id}/invitations/count`, `POST /accounts/{id}/invitations/withdraw`, `GET /accounts/{id}/invitations/withdraw/{task_id}`.
- Safe limit: 100 per session (LinkedIn tolerates this without triggering automation signals).

## Scheduled Auto-Withdraw

`withdraw_invitations_sweep` APScheduler job fires hourly; per-account logic enforces the configured interval.

**How it works:** skips accounts where `withdraw_threshold` is NULL; checks if `auto_withdraw_interval_days` have elapsed; reads live pending count via browser; if `live_count > withdraw_threshold`, withdraws oldest invitations targeting a randomised count just below the threshold (within 5%), capped at 200/session. Stamps `auto_withdraw_last_run` regardless of whether withdrawal was needed.

**Account model fields** (migration 017):
- `withdraw_threshold: Optional[int]` — trigger threshold; NULL = disabled
- `auto_withdraw_interval_days: int` — default 30; how often the sweep may fire per account
- `auto_withdraw_last_run: Optional[datetime]` — last successful sweep timestamp
- `pending_invitations_count: Optional[int]` — cached count from last sweep run

**Dashboard:** Account Settings → Invitations panel. Threshold + interval inputs save via the standard PUT `/accounts/{id}` endpoint. Last-run timestamp + cached count are shown read-only.

## Database Backup

`scripts/backup_db.sh` — uses the SQLite online backup API (`sqlite3.backup()`) which is WAL-safe and works on a live DB.

- **Daily local backup**: keeps the 7 most recent snapshots in `/root/linauto/data/backups/`. Run as: `bash /root/linauto/scripts/backup_db.sh`
- **Weekly offsite backup to Google Drive**: pass `--offsite` flag → `rclone copyto` uploads to `gdrive:releasi-backups/` (files older than 90 days are pruned from that folder). Cron on server: `0 3 * * 6` (Saturday 03:00 UTC).
- rclone config lives at `/root/.config/rclone/rclone.conf` (server only, not in repo). Remote is named `gdrive`.
- **The script fails loudly**: it integrity-checks each backup and exits non-zero (logging `BACKUP ERROR`) on any failure. `HOST_BACKUP_DIR` must stay the host path of the `/app/data` volume (`/root/linauto/data/backups`) — a wrong path once silently disabled pruning and offsite uploads for 4+ months. Check `grep -c "BACKUP ERROR" /root/linauto/data/backups/backup.log` after changing it.

To run a one-off offsite backup: `ssh root@<your-server-ip> 'bash /root/linauto/scripts/backup_db.sh --offsite'`


## Fundraising Agent

Weekly pipeline that scrapes CryptoRank and RootData for recently-funded crypto projects, enriches the team with Apollo + Telegram resolution, and uploads leads to a Releasi campaign.

- **Location**: `fundraising/fundraising.py` — runs **on the host** (not inside Docker)
- **Venv**: `/root/linauto/fundraising/venv/` — `pip install -r requirements.txt`
- **Systemd timer**: `fundraising-agent.timer` — fires every **Monday 09:00 UTC**
  - Check: `systemctl status fundraising-agent.timer`
  - Manual run: `cd /root/linauto/fundraising && venv/bin/python -u fundraising.py`
- **Config**: reads `config/settings.yaml` → `fundraising:` section (proxy, Apollo, Slack, Releasi keys).
- **DB access**: reads `scraper_cookies` table directly (WAL-safe read-only) for CryptoRank/RootData session cookies; reads `leads` table for dedup; writes `action_log` (type `fundraising_import`) after each successful run so the import appears in the campaign's Activity feed.
- **Role filters**: excludes CTO/engineering, HR, trading, sales, product, CFO/finance, legal/compliance from all three filter lists (CryptoRank scraper, Apollo search, Apollo merge).
- **Campaign**: hardcoded to `24acf14e-82ce-4ccd-826b-95739145d762` (configurable via `releasi_campaign_id` in settings.yaml).

## Scrapers (Cookie Refresh)

CryptoRank and RootData require valid session cookies to avoid CAPTCHAs. Cookies are stored in the `scraper_cookies` table (migration 026) and refreshed manually via the dashboard.

- **Dashboard**: `/scrapers` page — Globe icon in sidebar. Shows cookie age (green < 3d, yellow 3–5d, red > 5d). "Refresh Login" opens a noVNC browser (same Xvfb/websockify stack as LinkedIn login) for manual login, then "Save Cookies" extracts and stores all domain cookies.
- **API endpoints** (`/api/v1/scrapers`):
  - `GET /scrapers` — freshness status for both sites
  - `POST /scrapers/{site}/login-session` — start noVNC session
  - `POST /scrapers/{site}/login-session/finish` — extract + save cookies
  - `GET /scrapers/{site}/cookies` — fetch cookies (used by fundraising agent; also callable from localhost for other scripts)
- **Sites**: `cryptorank` (domain: `cryptorank.io`) and `rootdata` (domain: `rootdata.com`)
- **Conflict guard**: `ScraperSessionManager` refuses to start if a LinkedIn `LoginSessionManager` session is active (shared Xvfb/VNC port).

## Telegram Enrichment Sweep Status

The `/scrapers` page also hosts the **Telegram Enrichment** panel, which monitors the committed `tg_enrichment_sweep` scheduler job (one unenriched lead looked up every 20 min).

- **Endpoint**: `GET /api/v1/scrapers/tg-sweep/status` (`routes/scrapers.py`) — returns live sweeper state (`locked`, `flood_wait_until`/`flood_wait_remaining_seconds`, `last_processed_at`) plus `pending_count`/`searched_count`/`found_count` and the last 20 searched leads. Backed by `repo.get_tg_sweep_stats()` and `repo.get_recent_tg_sweep_results()`.
- **`last_processed_at` survives restarts**: it prefers the in-memory `_tg_last_processed_at` global (set each sweep) but falls back to the newest `tg_sweep_searched` event so a deploy doesn't blank it.
- **Panel fails loud, not silent**: on fetch error the `TelegramSweepPanel` shows a red "Backend unreachable" badge instead of falling back to a fake "Idle" — a 404/down endpoint must never look like a healthy idle sweeper. Don't reintroduce a silent `catch`.

## Lead-List Dispatch Priority

When a campaign has multiple assigned lead lists, `campaign_lead_lists.priority` (migration 030, higher = dispatched first) controls which list's pending leads go out first. `get_pending_leads_via_assignments` orders by list priority then `Lead.created_at`. Default 0 preserves legacy created-at ordering.

- Reorder via `PUT /api/v1/campaigns/{id}/lists/order` with `{ordered_list_ids: [...]}` (highest-priority-first → assigned `priority = n - index`).
- Dashboard: campaign **Lists** tab has ▲/▼ controls and a "Priority" badge on the top list.

## Phase Status
- Phase 1 (Foundation): COMPLETE — CLI, CSV import, template rendering, browser module
- Phase 2 (Scheduling & Safety): COMPLETE — Clustered planner, warmup, cooldown, APScheduler, stealth, noise, proxy/timezone
- Phase 3 (Follow-ups): IN PROGRESS — executor.execute_followup_sequence() implemented, dispatcher wired
- Phase 4 (Lead Detail & Enrichment): IN PROGRESS — Lead detail page, per-lead Telegram finder, social filter chips, outreach tracking (tg_contacted_at), lead_events activity feed, Twitter/X URL backfill
