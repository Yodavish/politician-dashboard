# Politician Dashboard

An end-to-end software engineering project for collecting and exploring publicly available U.S. politician stock trade disclosures. The system ingests and parses House Periodic Transaction Reports (PTRs), normalizes and stores the data in PostgreSQL, exposes it through a read-only FastAPI API, and provides a React + TypeScript dashboard for exploring the data.

The project also serves as a practical exploration of modern software engineering practices, including automated testing, CI/CD, static security analysis, dependency auditing, and automated dependency management.

## Live Demo

[Congress Trade Tracker](http://52.207.143.200/transactions) — currently served from the temporary EC2 instance's public IP. Custom domain and HTTPS are planned for a later milestone.

## Demo

https://github.com/user-attachments/assets/dab255d5-7eee-4e2a-a5af-341e3e981bb1

## Key Features

- House PTR disclosure ingestion and parsing
- Idempotent PostgreSQL data storage
- Read-only FastAPI REST API
- React + TypeScript web dashboard
- URL-based transaction filtering and pagination
- Automated backend and frontend testing
- GitHub Actions CI with PostgreSQL integration testing
- GitHub Actions continuous deployment to a single production EC2 instance over SSH
- CodeQL static security analysis
- Python and npm dependency vulnerability auditing
- Dependabot security updates and scheduled dependency updates

## Project Overview

This project collects publicly available House member stock trade disclosures, parses and normalizes the source data, stores the resulting records in PostgreSQL, exposes them through a read-only REST API, and serves a web dashboard (`dashboard/`) for exploring the data.

## Technology Stack

### Backend

- **Python 3.14**
- **FastAPI**
- **PostgreSQL 16**
- **psycopg**
- **pdfplumber**
- **pytest**
- **uv**

### Frontend

- **React**
- **TypeScript**
- **Vite**
- **Vitest**
- **Oxlint**
- **Tailwind CSS**
- **shadcn/ui**

### DevOps & Security

- **Docker / Docker Compose**
- **GitHub Actions**
- **GitHub CodeQL**
- **Dependabot**
- **uv audit**
- **npm audit**

## V1 Scope

- Daily-style ingestion of House PTR disclosure PDFs from the Clerk's Office.
- Parsing and normalization of transaction data.
- Storage in PostgreSQL (Postgres 16) via SQL migrations.
- A read-only FastAPI REST API (`/health`, `/politicians`, `/filings`, `/transactions`).
- A React + TypeScript web dashboard (`dashboard/`) that consumes that API.

## Architecture / Project Structure

```text
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
└── migrations/            # SQL migrations
    └── migrate.py        # migration runner

compose.yaml              # Docker service for PostgreSQL
pyproject.toml            # backend dependencies (managed by uv)
tests/                    # backend pytest suite + PDF fixtures
tools/ai_review/          # advisory AI code reviewer (GitHub API + Ollama model)

dashboard/                # Web dashboard (React + Vite + TypeScript)
├── src/api/              #   typed API response types + fetch wrapper
├── src/hooks/            #   URL filter state, politician-name lookup
├── src/components/       #   layout, health badge, pagination, states
├── src/pages/            #   transactions, politicians, profile, filing
└── src/lib/              #   formatting helpers
```

## Engineering & CI/CD

The project uses GitHub Actions to automatically validate changes through pull requests and pushes to `main`.

The CI pipeline includes:

- Python dependency installation with `uv`
- PostgreSQL 16 service for database-backed integration tests
- Backend pytest suite
- Frontend Vitest tests
- Frontend Oxlint
- TypeScript type checking
- Production frontend build
- Python dependency vulnerability auditing with `uv audit`
- npm dependency vulnerability auditing with `npm audit`
- Advisory AI-assisted code review on every pull request (see below)

The workflow uses least-privilege `GITHUB_TOKEN` permissions and grants the CI workflow only the repository access it requires.

### Advisory AI-assisted code review

An `ai-review` workflow complements the CI checks with an automated, **advisory** AI code review on every pull request (`opened`, `synchronize`, `reopened`). It runs on a GitHub-hosted `ubuntu-latest` runner, installs a local Ollama server, and uses the `qwen2.5-coder:7b` model to analyze the PR diff. Findings that resolve to an actual changed line are posted as inline review comments; the rest and the overall summary appear in the review body.

Review workflow security characteristics:

- **The review never blocks merging** — it always posts as a `COMMENT` event.
- **Pull request code is never checked out or executed.** The workflow checks out only the base branch (so the reviewer always runs the repository's own `tools/` code) and reads the diff and file contents through the GitHub REST API.
- **Limited permissions.** The job requests only `contents: read` and `pull-requests: write`; no other secrets are available, and PR content is sent to the model as untrusted data (prompt injection is treated as data, not instructions).
- **Fork and Dependabot pull requests** run with a read-only token; for those, the review is written to the workflow step summary instead of being posted, and the job never fails the pipeline.
- **No secret leakage.** The `GITHUB_TOKEN` is only ever sent as an `Authorization` header to `api.github.com`; model output is never executed as a command.

The reviewer itself lives in `tools/ai_review/` and runs with `python -m tools.ai_review`. Individual check runs are safe to repeat: a review is posted at most once per head commit.

### Continuous deployment (production)

A `Deploy to EC2` GitHub Actions workflow (`.github/workflows/cd.yml`) updates the single production EC2 instance after every push to `main`. It runs post-merge and is **not** a merge gate: CI and the advisory AI review remain the only pull-request checks.

How it works:

- The job connects to the EC2 instance over SSH using repository secrets only (no AWS access keys involved).
- On the instance it `git fetch`es `main`, checks out and hard-resets `/opt/politician-dashboard` to `origin/main`, then rebuilds the `api` and `web` images and recreates those two services with `docker compose`.
- PostgreSQL (`db`), its named `pgdata` volume, and the production `.env` are never touched: the workflow never runs `docker compose down` and never runs migrations or ingestion.
- Once the API responds end-to-end through Nginx (`curl --fail http://127.0.0.1/api/health`, retried for up to 5 minutes), the job prints the deployed commit and succeeds. A failed health check fails the job and reports the service status.

Required GitHub repository secrets (Settings → Secrets and variables → Actions):

| Secret | Contents |
| --- | --- |
| `EC2_HOST` | Public hostname or IP address of the EC2 instance. |
| `EC2_USER` | SSH user for the instance (`ec2-user` on Amazon Linux 2023). |
| `EC2_SSH_KEY` | Private key (PEM/OpenSSH format) used to connect, saved as a multi-line secret that preserves newlines. |
| `EC2_PORT` | SSH port; defaults to `22` when unset. |
| `EC2_KNOWN_HOSTS` | Optional but recommended: the instance host-key lines, e.g. the output of `ssh-keyscan <EC2_HOST>`, so SSH host-key verification is strict (`StrictHostKeyChecking=yes`). When unset, the workflow pins the host key observed on the first deployment (trust-on-first-use with `StrictHostKeyChecking=accept-new`), which still rejects a changed key on subsequent runs. |

Security properties:

- The workflow declares `permissions: {}`; it receives no `GITHUB_TOKEN` and can perform no repository actions.
- No `pull_request_target`, no AWS credentials, and no secret values are written anywhere in the repository.
- The private key is written to a throwaway `$HOME/.ssh/id_ec2` file on the ephemeral runner and used only inside the job.
- Only one deployment runs at a time (`concurrency` group); additional pushes queue rather than cancelling an in-flight deploy.

Manual steps stay on the instance. Apply SQL migrations when a release requires them with:

```bash
cd /opt/politician-dashboard
docker compose run --rm api python -m politician_dashboard.migrations.migrate
```

The House Clerk ingestion is likewise still run manually.

## Security & Dependency Management

The project uses several complementary security and dependency-management controls:

- **CodeQL** — static analysis for Python, JavaScript/TypeScript, and GitHub Actions.
- **uv audit** — checks Python dependencies for known vulnerabilities.
- **npm audit** — checks frontend dependencies for known npm vulnerabilities.
- **Dependabot alerts** — identifies dependencies with known security vulnerabilities.
- **Dependabot security updates** — creates pull requests for available security fixes.
- **Dependabot version updates** — weekly updates for Python, npm, and GitHub Actions dependencies.

Dependabot configuration is maintained in `.github/dependabot.yml`.

The CI workflow uses least-privilege GitHub Actions permissions with `contents: read`.

## Prerequisites

- **Python 3.14** (see `.python-version`)
- **uv** for dependency management
- **Docker** (with Compose) to run **PostgreSQL 16**
- **Node.js 22+** (with npm) for the dashboard

## Local Setup

```bash
# 1. Create your environment file from the template
cp .env.example .env
#    (edit the password / DATABASE_URL in .env as needed)

# 2. Install dependencies
uv sync

# 3. Start PostgreSQL
docker compose up -d
```

## Database Migrations

Apply pending SQL migrations (in filename order) with:

```bash
uv run --env-file .env python -m politician_dashboard.migrations.migrate
```

Migrations live in `politician_dashboard/migrations/*.sql` and are tracked in the `schema_migrations` table.

## Running the FastAPI API

The app is created by the `create_app` factory in `politician_dashboard/api/main.py`, so run uvicorn with `--factory`:

```bash
uv run --env-file .env uvicorn politician_dashboard.api:create_app --factory --host 0.0.0.0 --port 8000
```

## Swagger / OpenAPI Docs

Once the API is running, interactive docs are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## Running the Web Dashboard

The dashboard is a separate Vite + React + TypeScript app in `dashboard/`. Install its dependencies and start the dev server:

```bash
cd dashboard
npm install
npm run dev
```

With the API running on `http://127.0.0.1:8000`, open `http://localhost:5173`. The Vite dev server proxies `/api` to the backend (host configurable via the `API_TARGET` env var, default `http://127.0.0.1:8000`), so no CORS configuration is needed on the API.

Production build, tests, and lint:

```bash
npm run build     # typecheck (tsc -b) + production bundle
npm test          # vitest unit tests
npm run lint      # oxlint
```

## Running the House Clerk Ingestion CLI

Ingest the current year's House PTR disclosures:

```bash
uv run --env-file .env python -m politician_dashboard.ingest
```

Options:

- `--year YEAR` — ingest a single year (default: current year)
- `--backfill` — ingest every year from `--since` through the current year
- `--since YEAR` — starting year for `--backfill` (default 2011)
- `--database-url URL` — override `DATABASE_URL`

## Running the Test Suite

```bash
uv run --env-file .env pytest
```

The database-backed integration tests (storage, migrations, API) require a reachable PostgreSQL and `DATABASE_URL` (provided by `.env`) to run; they are skipped automatically otherwise. The parser and runner unit tests run without a database.

## Data Source / House PTR Explanation

Data comes from the **U.S. House of Representatives Office of the Clerk**, which publishes Members' Financial Disclosure statements as PDFs ("Periodic Transaction Reports", or PTRs). The indexed disclosures are available online for each year; each filing's PDF lists the member's security transactions.

The ingestion pipeline fetches the yearly index, downloads each PTR PDF, extracts and normalizes the transaction records, and stores them keyed by the filing's `doc_id` (idempotent on re-ingest).

## Important V1 Limitations

- **House Clerk only.** Data is sourced from the House only; Senate disclosures are not ingested.
- **Scanned/image-only filings are skipped.** Some disclosures are image-only PDFs with no embedded text; these cannot be parsed and are skipped (counted in the ingestion run).
- **`politician_id` is a derived V1 identity.** A politician is identified by normalized `state_district + first name + last name`; it is a convenience identifier for grouping and is **not** a permanent, authoritative politician identity.
- **No investment scoring or recommendations.** The project stores and serves raw disclosures only; it does not provide buy/sell assessments or scoring.
- **The API is read-only.** No write/update endpoints are exposed.

## Roadmap

- [x] End-to-end ingestion pipeline
- [x] PostgreSQL persistence
- [x] FastAPI read-only API
- [x] React dashboard
- [x] Automated CI
- [x] CodeQL security analysis
- [x] Dependency auditing
- [x] Dependabot
- [x] AI-assisted code review
- [x] Dockerized application deployment
- [x] AWS deployment
- [x] Continuous deployment workflow (SSH to EC2)
- [ ] Production health checks and monitoring

