# Forey Test backend

**THIS ENVIRONMENT EXISTS AND IS IN USE.** Created 2026-09-19 and running
since: `forey-test-api` and `forey-test-db` in `syd`, with the device
acceptance run going through them. It is not a plan any more.

Read this before running anything:

| If you are | Go to | Do NOT |
|---|---|---|
| Fixing the environment that exists | [Recovery](#recovery--the-environment-that-exists) | Run the creation sequence. It rotates `JWT_SECRET`, signing the test phone out mid-acceptance, and re-seeds the accounts, re-promoting the contributor the isolation test depends on |
| Building a new one from nothing | [Creation from scratch](#creation-from-scratch) | Run it while the current one is alive unless you mean to replace it |

The existing backend (`sitetracker-backend-staging.fly.dev`) carries the
operator's real business data and is untouched by every step below. Nothing
here reads it, writes to it, copies from it, or reuses any of its secrets.

**Every flyctl command in this document names its `--app` explicitly.** A
bare `flyctl` resolves the app from `fly.toml` in the working directory,
and this runbook's own sequence starts with `cd backend`, where
`backend/fly.toml` names **`sitetracker-backend-staging`**. A resize or a
restart typed without `--app` in that directory is aimed at the real
backend. If you find a command here without one, that is a defect in this
document — fix it before running it.

Decision of record: `docs/decisions/ADR-005-forey-test-app-variant.md`.

## The configuration

| | Value | Why this one |
|---|---|---|
| Provider | **Fly.io** | Same stack, CLI and deploy path as the existing backend; ADR 0003 already chose it |
| Region | **syd** | Same as staging; the phone testing it is in Australia |
| App name | **`forey-test-api`** | Distinct from `sitetracker-backend-staging`; the name is in `backend/fly.test.toml`, so a deploy cannot reach the real app by accident |
| Machine | **shared-cpu-1x, 512 MB**, `min_machines_running = 1`, `auto_stop_machines = off` | Mirrors staging's VM block. It must answer a phone on cellular with the laptop off, and one machine has to keep the evidence volume mounted |
| Database | **`forey-test-db`** — a Fly **unmanaged** Postgres app: `shared-cpu-1x`, **512 MB**, one 1 GB volume, region `syd` | One command, the same product the existing staging database was created with, and billed at machine + volume rates. 512 MB is a floor, not a preference — see below. Limits below |
| Attachment storage | **local adapter on a 1 GB Fly volume** `forey_test_evidence` mounted at `/data/evidence` (`EVIDENCE_STORAGE_BACKEND=local`, `EVIDENCE_LOCAL_ROOT=/data/evidence`) | A machine's own disk is not persistent; a volume is. **Deliberately not Tigris** — see the note below |
| Upload cap | **`EVIDENCE_MAX_UPLOAD_BYTES=52428800`** (50 MiB), a Fly secret on `forey-test-api` only | Matches the test variant's client limit (`mobile/app.config.ts` `UPLOAD_LIMITS.test`). Without it the server falls back to 26,214,400 (25 MiB) under a 50 MiB client. **The real Forey stays at 26,214,400 on both sides** — never copy this value to its backend, and never read a Forey Test upload result as proof about the real app |
| `APP_ENV` | **`test`** | The loader treats `test` as a NON-development environment: a real JWT secret (>= 32 chars, no placeholders) and no wildcard CORS are enforced. It is also the only value that permits the local storage adapter — `staging` and `production` force `s3` (`backend/app/config.py`) |
| `JWT_SECRET` | **generated fresh for this app** | Never the development placeholder, never a secret from the real environment |
| `CORS_ALLOWED_ORIGINS` | `https://forey-test-api.fly.dev` | A wildcard is rejected outside development, and a native app needs no browser origin |
| Code version | **`main`** (PR #19 merged as `bcc3964` on 2026-10-01; accepted on the device at `d52cf02`, tree-identical) | The real backend runs `6036491` (measured 2026-10-01), which has the Site Log tables but no Site Log API — see `docs/operations/production-release-plan.md` |
| Accounts | `admin@forey-test.example.com` and `worker@forey-test.example.com`, seeded by `scripts.seed_admin`, the second demoted to contributor | Permission isolation needs a non-admin. **Not a `.local` address**: `LoginRequest.email` is a Pydantic `EmailStr`, which rejects that reserved domain with 422 before authentication - verified against the repository's own schema |

### The database, and its limits

**One choice: a Fly unmanaged Postgres app**, created with
`flyctl postgres create`. It is a machine plus a volume, which is why it
costs about two dollars a month rather than tens.

What that buys, and what it does not:

- single node, single volume - no replica, no automatic failover;
- no managed backup schedule; nothing here is worth backing up, because
  every row in it is synthetic test data;
- it is a **short-term test database**, created for the device acceptance
  run and destroyed after it. It is not a staging database and must never
  hold real business data.

**If `flyctl postgres create` is not available in the installed CLI,
STOP and report it.** Do not substitute Fly Managed Postgres: its smallest
published plan is an order of magnitude more expensive, its `attach` and
`destroy` take a cluster id rather than a name, and paying that for a
few days of testing is a decision for the founder to make explicitly, not
a fallback for a script to take.

### Why 512 MB, and not 256 MB

**256 MB does not work.** The environment was created at 256 MB, passed the
full verification, and then died while idle: the machine stayed `started`,
but Postgres inside it stopped answering on 5433 and never came back. All
three Fly health checks went critical, `repmgrd` logged `connection to
database failed` on a loop, and the API returned 500 on every request, with
asyncpg raising `ConnectionDoesNotExistError` while *opening* a connection —
not on a stale pooled one, so `pool_pre_ping` could not help.

`flyio/postgres-flex` runs Postgres 18, `repmgr` and a metrics exporter in
one machine. 256 MB is below what that image needs, and the failure appears
only after the environment has been up a while, which is exactly when a
device test is running and the diagnosis costs the most.

A machine already in this state is recovered by the resize in
[Recovery](#recovery--the-environment-that-exists) below; it came back
3/3 healthy and the full verification passed again immediately.

### Why not Tigris here

Tigris is an unverified release gate. Running the device acceptance through
it would mix an unproven dependency into the app's own acceptance, so an
upload failure could not be attributed. The local adapter on a volume keeps
the two apart.

**This environment therefore proves the local-storage path only. The Tigris
and incomplete-multipart release gates remain UNVERIFIED and are not
touched by any result obtained here.**

## Cost estimate

Fly.io published list pricing, read 2026-09-19. These are list rates, not a
reading of the account — I have not logged in to it.

All figures in **USD**.

| Item | Rate | Monthly (USD) |
|---|---|---|
| App machine, shared-cpu-1x 512 MB, always on | ~$3.19 / mo | 3.19 |
| Evidence volume, 1 GB | $0.15 / GB / mo | 0.15 |
| Postgres machine, shared-cpu-1x 512 MB, always on | ~$3.19 / mo | 3.19 |
| Postgres volume, 1 GB | $0.15 / GB / mo | 0.15 |
| Egress | first 100 GB included | ~0 |
| **Total, left running** | | **~6.68 / month** |

Both machines are the same shape, so they are the same rate. An earlier
version of this table priced them at $3.19 and $3.89 and totalled 7.38; the
arithmetic was right and one of the line items was not.

Basis: Fly.io published list pricing for shared-cpu-1x machines and volume
storage, read 2026-09-19. **An estimate from list rates, not a reading of
the account** - I have not logged in to it, and providers change prices.
Machines bill per second, so a three-day acceptance run that is then
destroyed costs well under a dollar. Confirm on the Fly dashboard before
approving.

## Cleanup

```bash
# Look BEFORE destroying: after the app is gone this command has no app to
# report on, so running it afterwards proves nothing.
flyctl volumes list --app forey-test-api   # note what is there
flyctl volumes list --app forey-test-db

flyctl apps destroy forey-test-api
flyctl apps destroy forey-test-db          # an unmanaged Postgres is an app

# Volumes go with the app they belong to. Confirm against the account:
flyctl apps list                           # expect: neither forey-test app,
                                           # and sitetracker-backend-staging
                                           # STILL PRESENT
```

Nothing needs migrating back: the database, the volume and the secrets exist
only for this test.

## Recovery — the environment that exists

For an environment that is already running and has gone wrong. Nothing here
creates, seeds or rotates anything, so none of it can cost you the accounts
or the phone's session.

Run these from anywhere. Every command names its app, so the working
directory does not matter — which is the point, because `backend/fly.toml`
names the real backend.

```bash
# 1. Is it actually up? /healthz does NOT touch the database, so it stays
#    200 while Postgres is dead. This login probe does touch it:
#    401 = database answering, 500 = database down.
curl -sf https://forey-test-api.fly.dev/healthz
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST https://forey-test-api.fly.dev/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"nobody@forey-test.example.com","password":"wrong"}'

# 2. What the database machine thinks. Expect 3/3 passing.
flyctl checks list --app forey-test-db

# 2b. The upload cap is a secret, so a rebuilt or restored environment can
#     lose it silently. The NAME must be listed (the value is never shown):
flyctl secrets list --app forey-test-api
```

If `EVIDENCE_MAX_UPLOAD_BYTES` is missing, the server is refusing uploads
above 25 MiB that the Forey Test app offers up to 50 MiB. Setting it restarts
the machine and touches nothing else (not `JWT_SECRET`, not the accounts):

```bash
flyctl secrets set --app forey-test-api EVIDENCE_MAX_UPLOAD_BYTES=52428800
```

If the checks are critical and the login probe returns 500, the database
machine is up but Postgres inside it is not — the 256 MB failure described
above, or the same symptom from another cause.

```bash
# 3. Get the machine id. THIS is the safe form: it names the app, so it
#    cannot list the real backend's machines. Never run a bare
#    `flyctl machines list` while resolving a database incident.
flyctl machines list --app forey-test-db

# 4. Resize that machine. --app is what makes this safe; --yes skips the
#    confirmation, so the id must be one you just read from step 3.
flyctl machine update <db-machine-id> --app forey-test-db --vm-memory 512 --yes

# 5. Confirm, then re-run the full verification below - not just /healthz.
flyctl checks list --app forey-test-db
```

A restart, when the size is already right and Postgres is merely wedged:

```bash
flyctl machine restart <db-machine-id> --app forey-test-db
flyctl machine restart <api-machine-id> --app forey-test-api
```

Recovery is finished when section (b) of
[Verification](#verification--and-what-it-does-not-prove) passes, not when
`/healthz` returns 200.

## Creation from scratch

**Only for building a NEW environment.** Running this against the live one
rotates `JWT_SECRET` (which signs the test device out mid-acceptance) and
re-runs `seed_admin`, which resets an existing user's role to admin —
silently re-promoting the contributor account the permission-isolation test
depends on. To fix a running environment, use
[Recovery](#recovery--the-environment-that-exists) instead.

Every credential is the operator's.

```bash
cd backend

# 1. The app. --no-deploy so nothing starts before its secrets exist.
flyctl apps create forey-test-api
flyctl volumes create forey_test_evidence --app forey-test-api --region syd --size 1

# 2. The database, and attach it (this sets DATABASE_URL on the app).
#    If this command does not exist in the installed CLI: STOP and report.
#    512 MB, not the 256 MB default - see "Why 512 MB" above; 256 MB dies
#    while idle and takes the whole environment down with it.
flyctl postgres create --name forey-test-db --region syd \
  --vm-size shared-cpu-1x --volume-size 1 --initial-cluster-size 1

#    Read the machine id back - naming the app, so this cannot list the
#    real backend's machines - then resize before attaching anything.
flyctl machines list --app forey-test-db
flyctl machine update <db-machine-id> --app forey-test-db --vm-memory 512 --yes

#    The database is briefly live at the default size between those two
#    commands. That is tolerated only because nothing is using it yet;
#    wait for 3/3 before going on.
flyctl checks list --app forey-test-db

flyctl postgres attach forey-test-db --app forey-test-api

# 3. Secrets. Generate the JWT secret; do not reuse one from anywhere.
flyctl secrets set --app forey-test-api \
  APP_ENV=test \
  JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  CORS_ALLOWED_ORIGINS=https://forey-test-api.fly.dev \
  EVIDENCE_STORAGE_BACKEND=local \
  EVIDENCE_LOCAL_ROOT=/data/evidence \
  EVIDENCE_MAX_UPLOAD_BYTES=52428800

# 4. Deploy the accepted head, with the TEST config file.
git checkout <accepted-sha>
flyctl deploy --config fly.test.toml --app forey-test-api

# 5. Migrations are manual for V1, as they are for staging.
flyctl ssh console --app forey-test-api --command "alembic upgrade head"

# 6. Two synthetic accounts. Passwords exist only in this environment.
flyctl ssh console --app forey-test-api --command \
  "python -m scripts.seed_admin --email admin@forey-test.example.com --password '<generated>' --name 'Test Admin'"
flyctl ssh console --app forey-test-api --command \
  "python -m scripts.seed_admin --email worker@forey-test.example.com --password '<generated>' --name 'Test Worker'"
```

The second account must then be demoted to `contributor`; `seed_admin`
only makes admins. One statement over the attached database:

```sql
UPDATE users SET role = 'contributor' WHERE email = 'worker@forey-test.example.com';
```

## Verification — and what it does NOT prove

`/healthz` and an unauthenticated 401 are a **preliminary** check: they say
the app is up and the Site Log router is mounted. They say nothing about
whether a capture actually works.

```bash
API=https://forey-test-api.fly.dev

# (a) preliminary
# GET, not HEAD: /healthz is registered for GET only, and `curl -I`
# would send HEAD and get 405 from a perfectly healthy deployment.
curl -sf $API/healthz                                   # {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" $API/site-log-events/mine   # 401, NOT 404
```

A 404 means the deployed code predates the Site Log API: stop and deploy
the right version.

```bash
# (b) the real check: a capture, end to end, with test data only
TOKEN=$(curl -s -X POST $API/auth/login -H 'content-type: application/json' \
  -d '{"email":"admin@forey-test.example.com","password":"<generated>"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

CID=$(python -c "import uuid; print(uuid.uuid4())")
AID=$(python -c "import uuid; print(uuid.uuid4())")

# declare a capture with one declared image attachment
curl -s -X POST $API/site-log-events -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d "{
    \"capture_client_id\": \"$CID\", \"job_id\": null, \"occurred_at\": null,
    \"internal_location\": null, \"body_text\": \"test capture\",
    \"attachments\": [{\"attachment_client_id\": \"$AID\",
      \"declared_media_type\": \"image\", \"declared_size_bytes\": null}]}"

# upload real bytes to it (any small test image)
EVENT=<site_log_event_id from the response above>
curl -s -X PUT $API/site-log-events/$EVENT/attachments/$AID \
  -H "authorization: Bearer $TOKEN" -F "file=@./test.png;type=image/png"   # 201, state stored

# finalize, then read it back
curl -s -X POST $API/site-log-events/$EVENT/finalize -H "authorization: Bearer $TOKEN"
curl -s $API/site-log-events/$EVENT -H "authorization: Bearer $TOKEN"      # capture_status complete
curl -s "$API/site-log-events/mine?limit=5" -H "authorization: Bearer $TOKEN"
```

All five steps must pass before a Forey Test build is made. Passing them
proves the API, the database, the migrations and the **local** evidence
path. It does **not** clear the Tigris or incomplete-multipart release
gates, and it is not a substitute for the on-device acceptance run.
