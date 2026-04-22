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
Node 20+, and (optionally, for native mobile) the Expo Go app or a simulator.

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

### 2. Backend (FastAPI) — port 8000

```bash
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
# Create the separate test DB once per fresh Docker volume:
docker exec sitetracker-db psql -U sitetracker -d sitetracker -c "CREATE DATABASE sitetracker_test OWNER sitetracker;"
# Seed the first admin user + the 23 builder categories:
uv run python -m scripts.seed_admin --email admin@example.com --password admin --name "Admin"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# API on http://127.0.0.1:8000, interactive docs at /docs
```

### 3. Admin (Vite / React) — port 5173

```bash
cd admin
npm install
npm run dev
# Admin on http://127.0.0.1:5173
```

Sign in at `/login` with `admin@example.com` / `admin`. Create a job, add
aliases + category budgets, invite contributors from the Users page.

### 4. Mobile (Expo / React Native)

Pick one of the three targets below. Phase 1 was verified end-to-end via the
**web** target on Windows; native iOS/Android runs belong on a device with
Expo Go or a simulator.

```bash
cd mobile
npm install

# Option A — web preview (verified in Phase 1 on Windows)
npm run web                # opens http://127.0.0.1:8081

# Option B — Expo Go on a physical device (recommended for mobile QA)
npx expo start             # scan the QR code with Expo Go (iOS/Android)

# Option C — iOS Simulator (macOS + Xcode only)
npx expo start --ios
```

If running mobile on a physical device, point it at your LAN IP instead of
`127.0.0.1`:

```bash
EXPO_PUBLIC_API_URL=http://<your-lan-ip>:8000 npx expo start
```

Sign in on mobile with the same `admin@example.com` / `admin` credentials.
Jobs created from the admin dashboard appear immediately in the mobile Jobs
tab.

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

## Phase 1 end-to-end walkthrough

Exercises every wire in the stack. Requires all three servers running from
Quickstart above (Postgres on 5433, backend on 8000, admin on 5173).

1. Visit <http://127.0.0.1:5173/login> → sign in as `admin@example.com` / `admin`.
2. On the Jobs page, click **New Job** → fill `Kelly House`, `KH-01`, address, contract value `500000`, budget `450000` → **Save**.
3. Click the `Kelly House` row → on the detail page, add alias `Kelly` (language `EN`) → add category budget `Plumbing` / `25000`.
4. Navigate to **Users** → **Invite user** → create `jeffrey@example.com` / `contributor` / initial password `jeffpass`.
5. Switch the header language dropdown to **Chinese** → every label flips (项目 / 用户 / 退出登录 …).
6. Click **Log out** → redirected to `/login`; localStorage tokens are cleared.
7. Manually navigating to <http://127.0.0.1:5173/jobs> with no token redirects back to `/login` (auth-gate).
8. Start mobile web (`cd mobile && npm run web`) → visit <http://127.0.0.1:8081/login>.
9. Sign in with the same admin creds → the Jobs tab shows the `Kelly House` row created in step 2.
10. Settings tab → switch to 中文 → labels flip (项目 / 设置 / 当前用户 …) → **退出登录** clears tokens and routes back to `/login`.

## Where things live

```
.
├── backend/    FastAPI + SQLAlchemy 2 (async) + Alembic + Pydantic v2
├── mobile/     Expo SDK 54 + expo-router + TanStack Query + Zustand + i18next
├── admin/      Vite + React 18 + React Router + TanStack Query + Zustand + plain Tailwind + i18next
├── docs/       Product spec and per-phase implementation plans
├── scripts/    gen-types.ps1 / gen-types.sh (OpenAPI → TS types for both clients)
└── docker-compose.yml   Local Postgres (host 5433 → container 5432)
```

## Documentation

- [Product spec](docs/sitetracker-v1-spec.md) — the source of truth for scope, modules, and roles.
- Per-phase implementation plans live under `docs/` (e.g. `docs/phase-1-plan.md`, created in a later task).

## Languages

English (default) and Simplified Chinese. All user-facing strings live in
`i18n/` bundles per app.
