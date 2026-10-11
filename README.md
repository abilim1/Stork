# Stork

A private Micron and SanDisk dashboard: live OpenD prices, interactive charts, research with current news, weekly context, and a position journal.

## Start locally

From the project directory, using the Python environment that already runs the existing Moomoo tools:

```powershell
python -m stork.server --connect-live
```

Open **http://127.0.0.1:8765**. Omit `--connect-live` to browse saved history without connecting to OpenD. The Connect OpenD button can connect later. Stop the service with Ctrl+C.

Requirements: Python 3.10+, the existing `moomoo` SDK for live data, and New York timezone data (`tzdata` on Windows or the operating system timezone database on Linux). SQLite persistence and the frontend add no third-party dependencies. PostgreSQL storage uses the optional driver in `requirements-postgres.txt`. Existing desktop tools also use pandas and their existing GUI dependencies.

The existing `MMAPI.py`, `MMAPIGUI.py`, `NEWSGUI.py`, `core/`, and `gui/` workflows remain independent. Stork reads `saves/US_MU_15m.csv` and `saves/news_*.csv`; it does not rewrite them. By default, the SQLite database, settings, journal entries and research are stored under the Git-ignored `.stork/` directory. With PostgreSQL configured, database records live there instead; `.stork/` still holds local research working files and migration backups. CSV imports are checked at startup.

## Use the dashboard

Dark mode is the default. The sun/moon button in the top bar switches between dark and light. The choice is saved per browser and applied before the page paints; charts, dialogs and native form controls follow it.

- **Overview:** select 1D, 5D, 1M, 3M or all available history; select candle intervals from 1 minute through daily; switch between line, area, candlesticks and OHLC bars; filter sessions. Scroll over the chart to zoom, drag to pan, double-click or Reset view to fit the selected data. Keyboard controls: arrows, `+`, `-`, Home. View data provides a readable candle table.
- **Research:** Analyze now collects a market snapshot and runs web research through Codex signed in with ChatGPT. Reports retain their cutoff, linked sources, intraday and holding outlooks, ranked factors, changes from the preceding report, conditional scenarios and limitations. Earlier Moomoo news exports are explicitly archived keyword matches, not current research.
- **Position journal:** manually record the selected stock's quantity, entry and original intention. Review intention records why a trade becomes a holding. Closing records an already-completed trade. No order or trading-account API is used. P/L excludes fees and taxes and displays the valuation timestamp.
- **Preferences:** choose independent analysis cadences for premarket, regular, after-hours and optional overnight sessions; adjust research priorities totaling 100; select New York or Singapore display time; set price thresholds and optional browser notifications.

Automatic research is **off by default**. Turn it on after checking the ChatGPT connection and choosing your intervals. It uses the server's clock and OpenD's exchange state, skips unhealthy feeds and closed sessions, runs at most one report at a time, and saves each report. Missed runs are not backfilled. A saved slot prevents duplicate starts across restarts. Failed runs are visible and the next interval can retry. No separate OS scheduler is required.

Prices arrive through OpenD subscriptions; the service also refreshes quotes every 30 seconds. The browser refreshes its displayed state every 4 seconds and chart candles every 15 seconds. Individual received MU and SNDK ticks are stored; this is not a claim of complete consolidated market tape coverage. OpenD reconnects after a connection failure, with a 30-second delay.

## Stocks and report reading modes

Use the **Micron · MU** and **SanDisk · SNDK** tabs above the page heading. Both stay visible on every page; the selected stock is highlighted. Research and journal headings also identify the selected company. Prices, charts, weekly history, reports, positions, chart preferences, research weights, schedules and thresholds belong to the selected stock. The browser remembers your stock and explanation mode. The alert inbox is shared, with stock-labelled titles. OpenD controls affect both stocks. Existing unlabelled records remain Micron records; there is no destructive data migration. SanDisk history comes from recent OpenD backfill, not the older Micron CSV. The modern SNDK company must not be confused with pre-2016 history.

**Beginner / Detailed:** new reports generate both explanations from the same observations and sources. Old reports keep their detailed text and can generate a beginner explanation on demand from saved evidence, using subscription allowance without new web research. The original cutoff remains visible. Source links, list alignment and factor directions are validated. Model-written explanations still require review for factual accuracy.

Financial decimal figures are rounded to two places for display; calculations and snapshots retain full precision. Nonzero magnitudes below 0.005 display as less than 0.01 rather than implying exact zero. Dates, timestamps, URLs and whole-number counts are preserved. Short paragraphs, bullets and safe bold emphasis improve scanning without rendering arbitrary model-supplied HTML.

**News & sources** shows every recorded source, including news and factor references. Expand entries independently or use Expand all / Collapse all. Expansion survives automatic refresh and mode changes within the tab. New research requests every retrieved or attempted source, including excluded/background material, with access labels and relevance. The eight-item news cap is removed. Available search actions are retained separately. This is a researcher-recorded ledger, not a guaranteed complete export of search-engine results: the CLI does not expose every retrieved result. Old reports cannot recover unsaved sources.

Yahoo Finance and Bloomberg are explicit research targets alongside primary company and regulatory sources. Access is best-effort public web research, not a licensed publisher feed. Inaccessible material is labelled. Syndicated copies retain their original publisher and must not count as independent confirmation. No paid news product has been purchased or integrated.

Research runs one stock at a time. Each stock has its own saved schedule slot, with the least recently served eligible stock considered first. A healthy selected-stock quote is required for automatic research. Enabling both stocks increases subscription usage; SanDisk automatic research starts disabled.

Existing API calls default to MU. Stock-specific requests accept `?symbol=US.MU` or `?symbol=US.SNDK`; unknown stocks are rejected. The existing MU MCP tools remain, with read-only `get_sndk_snapshot` and `get_sndk_reports` added.

## ChatGPT subscription research

Install an official Codex CLI version supporting `exec --ignore-user-config`, `--output-schema` and `--output-last-message`, and sign in **with ChatGPT**, under the operating-system account that will run Stork:

```sh
codex login --device-auth
codex login status
```

An existing ChatGPT sign-in can be reused on this computer. The Linux server needs its own sign-in; do not copy credentials into this repository. Optional `STORK_CODEX_BIN` specifies the executable's absolute path when it is not on the service's PATH.

This integration uses **Codex with ChatGPT subscription authentication**, not browser automation of chatgpt.com. It checks the login method, forces the OpenAI provider and ChatGPT login, removes API-key environment variables from the child process, and does not fall back to separately billed API-key usage. Subscription allowances and account eligibility still apply. It runs with read-only sandboxing and live web search, ignores user-configured plugins/providers, validates the returned structure, and saves the result. A report has a ten-minute timeout.

The research input contains market data, timestamps, factor priorities and the previous report. Journal positions and private trading-account details are not included. Run the service as a dedicated OS user with only the filesystem access it needs; read-only sandboxing is not filesystem confidentiality isolation.

Official references: [Codex authentication](https://learn.chatgpt.com/docs/auth), [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), [live web search configuration](https://learn.chatgpt.com/docs/config-file/config-basic).

## Market data interpretation

- Prices and candles remain separately timestamped. A connected socket does not imply every benchmark has a fresh trade. Stale or missing prices are labelled; missing finer candles are never fabricated from 15-minute data.
- Existing and backfilled candles are QFQ-adjusted and end-labelled in New York time. A bar ending at 09:30 belongs to premarket; one ending at 16:00 belongs to regular trading. Aggregation keeps sessions separate and avoids double-counting overlapping 1-minute and 15-minute bars. Provider coarse candles remain authoritative where they overlap finer bars.
- Recent 15-minute history covers approximately 22 calendar days; 1-minute backfill covers three calendar days, subject to provider access and available bars. Older data comes from the existing CSV. Overnight candle history is collected while connected and may have gaps. Provider permissions, rate limits and outages can limit coverage.
- 1D/5D ranges mean the most recent one/five **observed New York dates**, including overnight observations. 1M and 3M select the latest 22 and 66 observed dates; they are approximate trading-month ranges. A date with only overnight data may have no regular-session candles.
- Premarket, after-hours and overnight percentages use the latest regular price as their reference. Before the next regular open, the provider's previous-close field can still refer to two sessions ago. Regular-session percentages use the provider's previous close. The separate regular-open card only compares against the regular session on that observation date.
- Weekly returns compare each week's last observed regular price with the preceding week's last observed regular close. An asterisk means fewer than five full sessions: a holiday, a gap or an unfinished week. This is a coverage flag, not an exchange-calendar completeness guarantee.
- The Friday record counts observed Friday 16:00 closes against the preceding Thursday close. Missing pairs and early-close Fridays are excluded. Its sample dates and definition appear on hover and in research input. It is descriptive history, not next-Friday odds.
- Weights guide research attention; they are not calibrated probabilities or measured contributions to the return. No directional probability model has been fitted. Research can be wrong; its claims and linked evidence remain reviewable.
- Corporate-action adjustments can change previously exported prices. This version does not reconcile an entire historical archive after a later split or adjustment. Re-export history with the existing tool when this occurs.

## Linux hosting

Copy the project and desired `saves/` exports to the server, provide its existing Python/Moomoo environment, and ensure OpenD is reachable privately. Check the installed SDK and CLI versions before enabling automation. Linux deployment has not been exercised by the local Windows test.

```sh
python3 -m stork.server --connect-live --opend-host 127.0.0.1 --opend-port 11111
```

Use `--opend-host` for a privately reachable OpenD address, or environment variables `STORK_OPEND_HOST` / `STORK_OPEND_PORT`. Keep brokerage OpenD access private. Use `--data-dir` for persistent storage and `--exports` for another export directory. Run one Stork process per database.

The safest initial remote access is an SSH tunnel with the service bound to loopback:

```sh
ssh -L 8765:127.0.0.1:8765 your-user@your-server
```

Then open http://127.0.0.1:8765 on your own computer. For an always-running service, adapt [`deploy/stork.service`](deploy/stork.service) to your user and installation paths. It is a template, not an installed service. Sign in to Codex under that same service user and provide the CLI path through the service environment when needed.

For LAN/remote binding, set a private, random `STORK_ACCESS_TOKEN` of at least 32 characters and place the service behind a properly configured HTTPS reverse proxy or private network. The token is sent as an Authorization bearer header and kept only in the browser tab's session storage. Preserve the incoming Host header at the proxy. Do not expose this standard-library HTTP server directly to the public Internet. TLS, ingress controls, request limits, domain setup and public deployment are not configured by this project.

Back up `.stork/` while the service is stopped (or use SQLite's online backup facility). Ticks and reports currently accumulate without automatic retention; size backups and storage accordingly. Browser notifications require the page to remain open. Email, Telegram and other external delivery channels are not configured.

## PostgreSQL on the VPS

Install the optional driver on the computer running Stork:

```sh
python -m pip install -r requirements-postgres.txt
```

`STORK_DATABASE_URL` selects PostgreSQL when using `python -m stork.server`.
Leaving it unset selects SQLite. There is **no automatic fallback** if PostgreSQL
is unavailable. Run only one Stork instance per database; the PostgreSQL backend
and migration acquire an exclusive application lock. Report bodies remain text
so migration preserves original JSON, source URLs, precision and both reading
modes exactly. Stock lookup indexes use JSON expressions.

For the existing PC-to-VPS setup, `python -m stork.vps_database` manages a private
SSH tunnel and retrieves credentials into memory from the VPS. It never writes
those credentials into the repository or command arguments. It requires a working
SSH key and an already trusted host alias (`vps` by default). PostgreSQL stays on
VPS loopback port 5432; no public database port or firewall changes are required.
The tunnel ends when the launcher exits. A broken connection causes explicit
errors; restart the launcher after connectivity returns. The PC must remain on.

### One-time administrator setup

Copy `deploy/setup-postgres.sh` to the VPS and run it **as the normal SSH user**.
It asks for sudo authentication privately, creates a dedicated `stork` login and
database, and writes generated credentials to `~/.config/stork/postgres.json`
with owner-only permissions. It refuses existing Stork roles/databases and never
changes other databases. If setup is interrupted, inspect its state before
retrying; the script deliberately does not overwrite its credential file.

### Migration and cutover

1. Complete database setup and install the driver before stopping the website.
2. Wait for active research to finish or cancel it through the dashboard, then
   stop the Stork server. Leave OpenD running. Stop any other SQLite writers.
3. From the project directory on the PC, use a **new, unique backup filename**:

   ```sh
   python -m stork.vps_database migrate --source .stork/stork.sqlite3 --backup .stork/backups/pre-postgres-YYYYMMDD-HHMMSS.sqlite3 --source-stopped
   ```

4. The tool makes a consistent SQLite backup, checks source integrity/schema,
   and copies all six tables in a PostgreSQL transaction. It compares record counts
   and SHA-256 digests of every ordered record, and advances generated ID sequences.
   A copy or verification failure rolls back the destination. Existing destination
   tables and existing backup files are never overwritten. Keep the printed
   verification manifest privately. A failed attempt can leave a backup file; use
   another filename when retrying. Do not start Stork against the destination first.
5. After the tool prints `verified`, launch the website:

   ```sh
   python -m stork.vps_database serve --connect-live
   ```

   Existing OpenD host/port and other server options can follow `serve`. This
   migration does not move OpenD, change its login, or deploy the website on Linux.
   If OpenD is running on the VPS, use `python -m stork.vps_database serve --vps-opend`
   instead. This forwards VPS loopback port 11111 to a private, dynamically selected
   local port and connects the feed automatically. Both tunnels share the launcher's
   lifetime; no public database or OpenD listener is added.
6. Check both stocks' report history, preferences, charts and journal. Confirm a
   new record saves and survives a server restart. Brief collection gaps during
   cutover may remain; this is not a guarantee of a gapless tick archive.

The original SQLite file is retained. Before PostgreSQL accepts new writes,
rollback consists of stopping the PostgreSQL launcher and starting the original
SQLite command. After new PostgreSQL writes, preserve/export them before any
rollback; a stale SQLite file does not include them. PostgreSQL backups must be
managed separately (for example with `pg_dump`); copying `.stork/` no longer backs
up the live database. Automatic PostgreSQL backup scheduling is not configured.

### PostgreSQL integration checks

`tests/test_postgres.py` uses disposable databases on an isolated PostgreSQL
instance. Set `STORK_TEST_DATABASE_URL` to an administrator connection whose
control database is named `stork_test_control`, then run the normal Python suite.
Tests create and remove only randomly named `stork_test_*` databases. Never supply
production credentials. Without this variable, PostgreSQL integration tests are
explicitly skipped; SQLite and other tests still run.

## Read-only tool endpoint

`POST /api/mcp` provides a small stateless MCP endpoint with `get_mu_snapshot` and `get_mu_reports`. It supports JSON responses, initialization, ping and tool discovery; `GET` returns 405 because it does not open an SSE stream. It implements a subset of the [2025-03-26 Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports) for single messages. Batch requests are not implemented. Local unauthenticated clients must send `X-Stork-Request: 1`; token-protected clients use `Authorization: Bearer …`.

This endpoint is tested locally. It has **not** been published, registered as a ChatGPT app, or tested through a ChatGPT connector. ChatGPT cloud access would need a reachable HTTPS endpoint and compatible authentication; a localhost address alone will not connect it. Scheduled Codex research works independently of this optional tool endpoint.

## Verification

```sh
python -m unittest discover -s tests -v
python -m compileall -q stork
node --check web/app.js
node --check web/chart.js
node --check web/theme.js
node --test tests/test_frontend.mjs
```

Tests use isolated temporary databases and local HTTP servers. They cover session boundaries, DST, overlapping candle resolutions, weekly/Friday references, extended quotes, stale tick handling, settings validation and persistence, journal history, thresholds, report validation, scheduler gating, credential exclusion, and HTTP access checks. They do not invoke OpenD or consume model usage. Node is only needed for the optional JavaScript syntax checks, not to run Stork.

Live verification additionally requires a connected OpenD and a ChatGPT-authenticated Codex CLI. Use Connection details and Check ChatGPT connection, then run one report and inspect the result. Loading the page alone is not evidence that live data or research works.
