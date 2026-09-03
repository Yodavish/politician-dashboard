# Politician Dashboard

An exploration of agentic AI development that builds an end-to-end pipeline
for publicly available U.S. politician stock trade disclosures. Data is
ingested and parsed, stored in PostgreSQL, exposed through a read-only
FastAPI API, and displayed in a React web dashboard.

## Demo
https://github.com/user-attachments/assets/2329764f-53da-4822-a3ad-4b7758d1a0ed

## Project overview

This project collects publicly available House member stock trade
disclosures, parses and normalizes the source data, stores the resulting
records in PostgreSQL, exposes them through a read-only REST API, and serves
a web dashboard (`dashboard/`) for exploring the data.

## Current V1 scope

- Daily-style ingestion of House PTR disclosure PDFs from the Clerk's Office.
- Parsing and normalization of transaction data.
- Storage in PostgreSQL (Postgres 16) via SQL migrations.
- A read-only FastAPI REST API (`/health`, `/politicians`, `/filings`, `/transactions`).
- A React + TypeScript web dashboard (`dashboard/`) that consumes that API.

## Architecture / project structure

```
politician_dashboard/
├── api/                  # Read-only FastAPI REST API
│   ├── routes/           #   health, filings, transactions, politicians
│   ├── main.py           #   application factory (create_app)
│   ├── queries.py        #   parameterized SQL data access
│   ├── schemas.py        #   response models
│   └── ...
├── config.py             # DATABASE_URL from the environment
├── db.py                 # psycopg connection helper
├── ingest/               # Ingestion pipeline
│   ├── parser.py         #   PDF -> normalized transactions
│   ├── runner.py         #   per-year orchestration
│   ├── store.py          #   filing + transactions persistence
│   ├── sources/          #   House Clerk index/PDF source
│   └── __main__.py       #   CLI entrypoint
└── migrations/           # SQL migrations (0001, 0002, 0003)
    └── migrate.py        # migration runner

compose.yaml              # Docker service for PostgreSQL
pyproject.toml            # backend dependencies (managed by uv)
tests/                    # backend pytest suite + PDF fixtures

dashboard/                # Web dashboard (React + Vite + TypeScript)
├── src/api/              #   typed API response types + fetch wrapper
├── src/hooks/            #   URL filter state, politician-name lookup
├── src/components/       #   layout, health badge, pagination, states
├── src/pages/            #   transactions, politicians, profile, filing
└── src/lib/              #   formatting helpers
```

## Prerequisites

- **Python 3.14** (see `.python-version`)
- **uv** for dependency management
- **Docker** (with Compose) to run **PostgreSQL 16**
- **Node.js 22+** (with npm) for the dashboard

## Local setup

```sh
# 1. Create your environment file from the template
cp .env.example .env
#    (edit the password / DATABASE_URL in .env as needed)

# 2. Install dependencies
uv sync

# 3. Start PostgreSQL
docker compose up -d
```

## Database migrations

Apply pending SQL migrations (in filename order) with:

```sh
uv run --env-file .env python -m politician_dashboard.migrations.migrate
```

Migrations live in `politician_dashboard/migrations/*.sql` and are tracked in
the `schema_migrations` table.

## Running the FastAPI API

The app is created by the `create_app` factory in
`politician_dashboard/api/main.py`, so run uvicorn with `--factory`:

```sh
uv run --env-file .env uvicorn politician_dashboard.api:create_app --factory --host 0.0.0.0 --port 8000
```

## Swagger / OpenAPI docs

Once the API is running, interactive docs are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## Running the web dashboard

The dashboard is a separate Vite + React + TypeScript app in `dashboard/`.
Install its dependencies and start the dev server:

```sh
cd dashboard
npm install
npm run dev
```

With the API running on `http://127.0.0.1:8000`, open
`http://localhost:5173`. The Vite dev server proxies `/api` to the backend
(host configurable via the `API_TARGET` env var, default
`http://127.0.0.1:8000`), so no CORS configuration is needed on the API.

Production build, tests, and lint:

```sh
npm run build     # typecheck (tsc -b) + production bundle
npm test          # vitest unit tests
npm run lint      # oxlint
```

## Running the House Clerk ingestion CLI

Ingest the current year's House PTR disclosures:

```sh
uv run --env-file .env python -m politician_dashboard.ingest
```

Options:

- `--year YEAR` — ingest a single year (default: current year)
- `--backfill` — ingest every year from `--since` through the current year
- `--since YEAR` — starting year for `--backfill` (default 2011)
- `--database-url URL` — override `DATABASE_URL`

## Running the test suite

```sh
uv run --env-file .env pytest
```

The database-backed integration tests (storage, migrations, API) require a
reachable PostgreSQL and `DATABASE_URL` (provided by `.env`) to run; they are
skipped automatically otherwise. The parser and runner unit tests run without
a database.

## Data source / House PTR explanation

Data comes from the **U.S. House of Representatives Office of the Clerk**,
which publishes Members' Financial Disclosure statements as PDFs ("Periodic
Transaction Reports", or PTRs). The indexed disclosures are available online
for each year; each filing's PDF lists the member's security transactions.

The ingestion pipeline fetches the yearly index, downloads each PTR PDF,
extracts and normalizes the transaction records, and stores them keyed by the
filing's `doc_id` (idempotent on re-ingest).

## Important V1 limitations

- **House Clerk only.** Data is sourced from the House only; Senate
  disclosures are not ingested.
- **Scanned/image-only filings are skipped.** Some disclosures are image-only
  PDFs with no embedded text; these cannot be parsed and are skipped (counted
  in the ingestion run).
- **`politician_id` is a derived V1 identity.** A politician is identified by
  normalized `state_district + first name + last name`; it is a convenience
  identifier for grouping and is **not** a permanent, authoritative politician
  identity.
- **No investment scoring or recommendations.** The project stores and serves
  raw disclosures only; it does not provide buy/sell assessments or scoring.
- **The API is read-only.** No write/update endpoints are exposed.
