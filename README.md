# Politician Dashboard

Politician Dashboard collects publicly available U.S. House and Senate stock-trade disclosures, preserves source data in PostgreSQL, and provides a read-only API and web dashboard for exploring filings and transactions. It does not provide investment scoring or buy/sell recommendations.

## Architecture

House Clerk PTR PDFs and Senate eFD disclosures are fetched and parsed by the Python ingestion pipeline. Filings and transactions are stored in PostgreSQL and served by FastAPI to a React dashboard.

Production deployment uses GitHub Actions to build the API and web images, tag them with the full Git SHA, and push them to ECR. The workflow passes that SHA to EC2 through SSM; EC2 pulls the matching images and runs them with Docker Compose. PostgreSQL remains on EC2 with its persistent `pgdata` volume.

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

`.env` defines `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `DATABASE_URL`. The Compose database uses the first three values; backend commands use `DATABASE_URL` (default template URL points to `127.0.0.1:5432`). Keep the credentials and URL in sync. Compose also requires `IMAGE_TAG` because the API and web services reference tagged ECR images; for a database-only local startup, any placeholder value satisfies Compose interpolation:

```bash
uv sync
IMAGE_TAG=local docker compose up -d db
uv run --env-file .env python -m politician_dashboard.migrations.migrate
```

### Run the API

```bash
uv run --env-file .env uvicorn politician_dashboard.api:create_app \
  --factory --host 0.0.0.0 --port 8000
```

Health endpoint: `http://localhost:8000/health`.

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

GitHub Actions builds and pushes API and web images tagged with the full 40-character Git SHA. SSM passes that SHA to the EC2 deployment script as `IMAGE_TAG`; EC2 authenticates to ECR, pulls both images, and updates the API and web services. PostgreSQL remains on EC2 with its persistent `pgdata` volume. CI/CD details live in `.github/workflows/ci.yml` and `.github/workflows/cd.yml`.
