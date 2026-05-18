# Staging deploy runbook

Procedural reference for the first deploy of the SiteTracker backend
to a staging environment on Fly.io.

Consumed by Slice 2-B2 (the actual deploy turn). Slice 2-B1 (the doc
batch this file ships in) does NOT execute any commands here.

Per [ADR 0003](../adr/0003-staging-deployment-strategy.md): staging
only; production deploy is a separate later slice.

Every command in this runbook is preceded by an explicit operator
**APPROVAL** gate. No gate may be skipped or combined with another
into a single operator action.

## Prerequisites (one-time operator setup; NOT a Slice 2-B2 gate)

- `flyctl` CLI installed locally.
  - Windows PowerShell: `iwr https://fly.io/install.ps1 -useb | iex`
  - macOS: `brew install flyctl`
- Fly.io account with billing on the operator's preferred org.
- `flyctl auth login` executed; `flyctl auth whoami` confirms the
  right account.
- Domain ownership confirmed for the eventual custom domain, OR
  acceptance that staging uses the default `<app>.fly.dev` URL.
- Apple Developer Program enrollment started in parallel (not a
  blocker for backend deploy; required for the eventual TestFlight
  slice).

## Gate D-1: Create the Fly app

**APPROVAL REQUIRED** before running the command.

```
flyctl apps create sitetracker-backend-staging --org <org-slug>
```

The `-staging` suffix is mandatory for environment self-description.
Creates app metadata in Fly; no billable resources yet.

**Post-step verification.** `flyctl apps list` shows
`sitetracker-backend-staging`.

## Gate D-2: Set primary region

**APPROVAL REQUIRED** before running the command.

```
flyctl regions set syd --app sitetracker-backend-staging
```

Per ADR 0003: Sydney is primary. If `syd` is unavailable for Fly
compute or Postgres at execution time (capacity-constrained or marked
preview), STOP — fall back per ADR 0003's Render Singapore alternative
and amend ADR 0003 in a separate batch.

## Gate D-3: Create managed Postgres

**APPROVAL REQUIRED** before running the command. Creates a billable
resource.

```
flyctl postgres create \
  --name sitetracker-pg-staging \
  --region syd \
  --vm-size shared-cpu-1x \
  --volume-size 10
```

Single-node managed Postgres cluster in `syd` with a 10 GB volume.
The admin connection string prints once; the operator records it
offline (NOT in git, NOT in chat).

**Post-step verification.** `flyctl postgres list` shows
`sitetracker-pg-staging` in `syd`.

## Gate D-4: Attach Postgres to the backend app

**APPROVAL REQUIRED** before running the command.

```
flyctl postgres attach sitetracker-pg-staging --app sitetracker-backend-staging
```

Auto-creates a dedicated DB + user for the backend; auto-injects
`DATABASE_URL` as a Fly secret on the backend app.

**Post-step verification.** `flyctl secrets list --app
sitetracker-backend-staging` shows `DATABASE_URL` as a secret name.
Values are NEVER printed by `flyctl secrets list`.

## Gate D-5: Set remaining secrets

**APPROVAL REQUIRED** before running the command. Handles real
`JWT_SECRET`; do not paste the value into chat.

```
flyctl secrets set \
  APP_ENV=staging \
  JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  CORS_ALLOWED_ORIGINS="https://<staging-admin-host>" \
  --app sitetracker-backend-staging
```

Notes:

- `JWT_SECRET` is generated inline via `python -c` so the literal
  value never appears in the operator's shell history.
- `CORS_ALLOWED_ORIGINS` initially uses a placeholder host; update
  with the actual staging admin URL when it exists.
- `APP_ENV=staging` triggers Slice 1's non-dev validator (placeholder
  / short secret rejected; empty / wildcard CORS rejected;
  `APP_ENV`/`ENVIRONMENT` conflict rejected).

**Post-step verification.** `flyctl secrets list --app
sitetracker-backend-staging` shows `APP_ENV`, `DATABASE_URL`,
`JWT_SECRET`, `CORS_ALLOWED_ORIGINS`.

## Gate D-6: Deploy backend

**APPROVAL REQUIRED** before running the command.

```
cd backend
flyctl deploy --app sitetracker-backend-staging
```

`flyctl deploy` remotely builds the Docker image on Fly's builders
(using the existing `backend/Dockerfile`) and rolls it out. Build is
cached on subsequent deploys when only `app/` changes.

**Post-step verification.** `flyctl status --app
sitetracker-backend-staging` shows the machine in `running` state in
`syd`.

## Gate D-7: Run alembic upgrade head

**APPROVAL REQUIRED** before running the command. **First DB write to
the staging Postgres.**

```
flyctl ssh console --app sitetracker-backend-staging
# inside the container:
cd /app && uv run alembic upgrade head
exit
```

Per ADR 0003: manual alembic for V1 — operator observes the migration
output on first run.

**Post-step verification.** `flyctl postgres connect --app
sitetracker-pg-staging` → `\dt` shows expected tables (`jobs`,
`expenses`, `expense_review_queue`, `job_audit_log`, etc.). `SELECT
version_num FROM alembic_version;` returns the current head SHA.

## Gate D-8: Seed staging admin

**APPROVAL REQUIRED** before running the command. The staging admin
email MUST be different from local dev's `admin@example.com` to
prevent environment confusion.

```
flyctl ssh console --app sitetracker-backend-staging
# inside the container:
cd /app && uv run python -m scripts.seed_admin \
  --email <staging-admin-email> \
  --password "$(python -c 'import secrets; print(secrets.token_urlsafe(16))')" \
  --name "Staging Admin"
exit
```

The generated password prints once to the operator's terminal; record
it ONCE in the operator's password manager. Idempotent — re-running
this command resets the named admin.

**Post-step verification.** `flyctl postgres connect --app
sitetracker-pg-staging` → `SELECT email, role FROM users WHERE
role='admin';` returns the seeded admin.

## Gate D-9: Smoke

**APPROVAL REQUIRED** before each smoke step.

Health check:

```
curl -sI https://sitetracker-backend-staging.fly.dev/healthz
```

Expect: `HTTP/2 200` + valid LetsEncrypt cert chain.

Login smoke:

```
curl -X POST https://sitetracker-backend-staging.fly.dev/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"<staging-admin-email>","password":"<password>"}'
```

Expect: 200 + `access_token` in response.

Positive CORS smoke:

```
curl -I -X OPTIONS https://sitetracker-backend-staging.fly.dev/jobs \
  -H "Origin: https://<staging-admin-host>" \
  -H "Access-Control-Request-Method: GET"
```

Expect: 200 + `Access-Control-Allow-Origin:
https://<staging-admin-host>`.

Negative CORS smoke:

```
curl -I -X OPTIONS https://sitetracker-backend-staging.fly.dev/jobs \
  -H "Origin: https://attacker.example.com" \
  -H "Access-Control-Request-Method: GET"
```

Expect: 200 but NO `Access-Control-Allow-Origin` header for the
attacker origin.

## Gate D-10: Close-out

Report:

- Live backend URL.
- Backend deploy SHA (matches the agent worktree HEAD).
- Live DB cluster name + region.
- Verification results from D-9.
- Confirm: NO production deploy was attempted in this batch.

After close-out, Slice 2-B2 is complete. Slice 2-B3 (backup/restore
rehearsal — `staging-backup-restore.md`) is a separate user-gated
batch.

## Hard boundaries (apply to every gate)

- NO production environment. NO production data.
- NO TestFlight build (separate slice).
- NO CI/CD automation (manual deploy from operator's machine for V1).
- NO Sentry / logging aggregation integration (separate future
  slice).
- NO real secrets manager (Vault / Doppler) — Fly's built-in is
  sufficient for V1.
- NO custom domain DNS finalization (default `<app>.fly.dev` is
  acceptable for staging).
- Each gate requires explicit operator approval BEFORE the command
  runs.
