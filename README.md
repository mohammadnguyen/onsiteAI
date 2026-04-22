# SiteTracker V1

Internal cost-control and expense-tracking app for a small residential builder. A
monorepo with three apps that share one product spec: a FastAPI backend, an
Expo/React Native mobile app for on-site capture, and a Vite/React admin web for
management. Supports English and Simplified Chinese. See
[`docs/sitetracker-v1-spec.md`](docs/sitetracker-v1-spec.md) for the full spec.

## Phase roadmap

| Phase | Scope                                                                                                     | Outcome                                                                                   |
| ----- | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 1     | Monorepo + backend foundation + auth + users + jobs + categories + thin mobile/admin shells              | Admin can invite users, create jobs with aliases/budgets; users log in on mobile and see jobs. |
| 2     | Expense model + hybrid NL parser + review queue                                                           | Users type `$305 Bunnings Kelly bluemetal` and save; low-confidence items land in review. |
| 3     | Dashboards (job list cards + job detail + top-N + monthly trend + margin)                                 | Real numbers per job.                                                                     |
| 4     | Excel export (All Expenses sheet + one sheet per job)                                                     | Accountant handoff works.                                                                 |
| 5     | Attachments (upload, S3-compatible storage, OCR-ready placeholder) + labour attendance UI                 | Receipts + labour days.                                                                   |
| 6     | TestFlight + prod backend deploy + bilingual QA pass                                                      | Builders use it on job sites.                                                             |

## Quickstart

Requirements: Docker Desktop, Python 3.12 + [`uv`](https://docs.astral.sh/uv/),
Node 20+, and the Expo Go app (for mobile dev).

### 1. Start Postgres

```bash
docker compose up -d db
```

Postgres is exposed on **host port `5433`** (container port stays 5432) to avoid
clashing with a native Postgres install, which is common on Windows dev
machines. All configuration defaults — `backend/.env.example`,
`backend/tests/conftest.py` — already use 5433. Inside the compose network, the
backend service still talks to the DB as `db:5432`; only host-side tooling
(`uv run alembic …`, host-run pytest) uses 5433.

### 2. Backend (FastAPI)

```bash
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
# API on http://localhost:8000, docs at /docs
```

### 3. Mobile (Expo / React Native)

```bash
cd mobile
npm install
npx expo start
# Scan the QR code with Expo Go (iOS or Android)
```

### 4. Admin (Vite / React)

```bash
cd admin
npm install
npm run dev
# Admin on http://localhost:5173
```

### Regenerate TypeScript API types

The mobile and admin apps share a generated TypeScript client contract at
`mobile/src/api/types.ts` and `admin/src/api/types.ts`. After changing any
backend schema or endpoint, regenerate both files so the frontends stay in
sync with the API.

```powershell
# Windows (primary — this repo's canonical script)
pwsh scripts/gen-types.ps1
```

```bash
# macOS / Linux / WSL / Git Bash fallback (also used in CI)
bash scripts/gen-types.sh
```

Both scripts hit `http://localhost:8000/openapi.json`, so make sure the
backend is running locally first (`uv run uvicorn app.main:app --reload`
from `backend/`). The scripts write identical output to both `mobile/` and
`admin/`, and those generated files should be committed whenever the API
changes. Regeneration is manual for now; a pre-commit hook is planned for
Phase 2.

## Where things live

```
.
├── backend/    FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2
├── mobile/     Expo SDK 52 + expo-router + TanStack Query + i18next
├── admin/      Vite + React 18 + TanStack Query + Tailwind + shadcn/ui
├── docs/       Product spec and per-phase implementation plans
├── scripts/    Monorepo-level helper scripts
└── docker-compose.yml   Local Postgres (and later, containerised backend)
```

## Documentation

- [Product spec](docs/sitetracker-v1-spec.md) — the source of truth for scope, modules, and roles.
- Per-phase implementation plans live under `docs/` (e.g. `docs/phase-1-plan.md`, created in a later task).

## Languages

English (default) and Simplified Chinese. All user-facing strings live in
`i18n/` bundles per app.
