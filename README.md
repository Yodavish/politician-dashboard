# Politician Dashboard

Politician Dashboard collects publicly available U.S. House and Senate stock-trade disclosures, preserves source data in PostgreSQL, and provides a read-only API and web dashboard for exploring filings and transactions. It does not provide investment scoring or buy/sell recommendations.

## Architecture

House Clerk PTR PDFs and Senate eFD disclosures are fetched and parsed by the Python ingestion pipeline. Filings and transactions are stored in PostgreSQL and served by FastAPI to a React dashboard.

Production deployment uses GitHub Actions to build the API and web images, tag them with the full Git SHA, and push them to ECR. The workflow passes that SHA to EC2 through SSM; EC2 pulls the matching images and runs them with Docker Compose. PostgreSQL runs on Amazon RDS in the production VPC.

## Live Demo

[Congress Trade Tracker](http://52.207.143.200/transactions) — currently served from the EC2 instance's public IP. A custom domain and HTTPS are planned.

## Technology stack

- **Backend:** Python 3.14, FastAPI, psycopg, PostgreSQL 16, pdfplumber, pytest, uv
- **Frontend:** React, TypeScript, Vite, Vitest, Oxlint, Tailwind CSS, shadcn/ui
- **Tooling:** Docker Compose, GitHub Actions, CodeQL, Dependabot

## Project structure

```text
politician_dashboard/
├── api/                 # FastAPI routes, queries, and response schemas
├── ingest/              # House/Senate parsers, source adapters, runner, storage
├── migrations/          # SQL schema migrations and migration runner
├── config.py, db.py     # Environment configuration and DB connections
dashboard/               # React/Vite app, API client, pages, and components
tests/                   # Backend tests and source-document fixtures
compose.yaml             # Local PostgreSQL and application services
tools/ai_review/         # Advisory pull-request review tool
```

## Local development

### Prerequisites

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- Docker with Compose (for PostgreSQL 16)
- Node.js 22+ and npm (for the dashboard)

### Configure and start the database

Copy the environment template and set a local database password and matching URL:

```bash
cp .env.example .env
```

`.env` defines `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `DATABASE_URL`. The standalone local Compose file uses the first three values; host-based backend commands use `DATABASE_URL` (the template points to `127.0.0.1:5432`). Keep the credentials and URL in sync. The local database file is not loaded by the production Compose command:

```bash
uv sync
docker compose -f compose.local.yml up -d db
uv run --env-file .env python -m politician_dashboard.migrations.migrate
```

### Run the API

```bash
uv run --env-file .env uvicorn politician_dashboard.api:create_app \
  --factory --host 127.0.0.1 --port 8000
```

Health endpoint: `http://localhost:8000/health`.

The API records HTTP server spans and request-duration metrics. PostgreSQL
operations are traced through psycopg instrumentation. Telemetry is not sent
anywhere unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set; the endpoint and optional
headers/protocol use the standard `OTEL_EXPORTER_OTLP_*` environment variables.
For local development, leave these variables unset and the API starts without
an exporter or telemetry backend. To export traces and metrics, set the endpoint
and, if needed, `OTEL_EXPORTER_OTLP_PROTOCOL` to `grpc` or `http/protobuf`.

### Run the dashboard

```bash
cd dashboard
npm ci
npm run dev
```

Open `http://localhost:5173`. The Vite server proxies `/api` to `http://127.0.0.1:8000` by default; set `API_TARGET` to use another API host.

## API

The API is read-only. Main resources are `/health`, `/politicians`, `/filings`, and `/transactions`. Interactive documentation and the OpenAPI schema are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

Example:

```bash
curl 'http://localhost:8000/transactions'
curl 'http://localhost:8000/filings'
```

## Ingestion

Ingestion defaults to the House Clerk source. Select Senate eFD explicitly; sources are never auto-detected:

```bash
# Current year, House (default)
uv run --env-file .env python -m politician_dashboard.ingest

# A single year or a year-range backfill
uv run --env-file .env python -m politician_dashboard.ingest --year 2025
uv run --env-file .env python -m politician_dashboard.ingest --backfill --since 2011

# Senate eFD
uv run --env-file .env python -m politician_dashboard.ingest --source senate --year 2025
```

Important behavior:

- Re-ingestion is idempotent by source filing ID and stores source provenance with filings and ingestion runs.
- Source-reported transaction dates and values are preserved. Quality flags describe inconsistencies without rewriting source data.
- Senate electronic filings are parsed from the eFD portal. Senate paper filings are scans without a text layer, so they are counted and skipped rather than fabricated from images.
- Senate filing IDs distinguish electronic UUIDs from numeric paper IDs. Senators use the `<STATE>00` district convention; unresolved state data raises an error rather than being guessed.
- Senate has no transaction notification date on the detail page, so its listing “Date Received” is used for the normalized filing and notification dates.
- Live Senate ingestion is opt-in and is not part of the scheduled pipeline.

The CLI also supports source-only quality-flag recomputation (`--recompute-flags --as-of YYYY-MM-DD`) and explicit, provenance-required curation of verified transaction dates and amendment relationships. Curation targets a single transaction or an explicit pair of stored filings; source status such as `Amended` is a review signal and does not create amendment links automatically. Run `uv run --env-file .env python -m politician_dashboard.ingest --help` for options.

## Tests and checks

```bash
# Backend; database-backed tests require PostgreSQL and DATABASE_URL
uv run --env-file .env pytest

# Frontend (from dashboard/)
npm test
npm run lint
npm run build   # TypeScript typecheck and production bundle
```

CI runs backend tests with PostgreSQL, frontend tests, lint, typecheck/build, and dependency audits. Parser and runner unit tests can run without a database.

## Production deployment

```text
GitHub → GitHub Actions → Docker build → ECR → SSM → EC2 → Docker Compose
```

GitHub Actions builds and pushes API and web images tagged with the full 40-character Git SHA. SSM passes that SHA to the EC2 deployment script as `IMAGE_TAG`; EC2 authenticates to ECR, pulls both images, and updates the API and web services. PostgreSQL runs on Amazon RDS in the production VPC. CI/CD details live in `.github/workflows/ci.yml` and `.github/workflows/cd.yml`.

## Future work

### Revisiting the buy-cluster 14-day span cap

`BUY_CLUSTER_MAX_SPAN_DAYS` in `politician_dashboard/api/signal_rules.py` is currently 14. A read-only investigation of live data considered raising it to 30 and found the following, for reference before any future change:

- The 7-day gap rule is not what excludes wider groups. Widening the *span* cap admits bursts whose internal gaps are already all ≤ 7 days; the cap alone is doing the filtering.
- Raising the cap to 30 adds 23 signals (50 → 73), concentrated in a few mega-cap tickers: AMZN ×4, MSFT ×4, NVDA ×3.
- Quality is unchanged by the widening: 73% of transactions in the added set fall in the $1,001–$15,000 minimum disclosure bucket, versus 75% in the existing set.
- Widening the *gap* rule to 30 days instead is clearly worse and should be avoided: it yields 37 mostly-minimum-bucket groups with a median of 1.00 transactions per person across 32 tickers.

The genuinely interesting case a 30-day cap would surface is large disclosed positions, e.g. AVGO 2025-06-06 (6 people, disclosed max $5M) and MSFT 2025-01-24 (7 people, disclosed max $5M).

Because of that last point, a future revision should consider distinguishing signal *quality* rather than simply widening the window. Note that changing the cap affects the API, the signal card, and the detail page copy — the "how this signal was triggered" text states the 14-day rule to users and would need to stay in sync.
