# Forey Test backend — the configuration to create

**Nothing here has been created, deployed or migrated.** This is the plan
to approve. One configuration is specified; there is no choice left open.

The existing backend (`sitetracker-backend-staging.fly.dev`) carries the
operator's real business data and is untouched by every step below. Nothing
here reads it, writes to it, copies from it, or reuses any of its secrets.

Decision of record: `docs/decisions/ADR-005-forey-test-app-variant.md`.

## The configuration

| | Value | Why this one |
|---|---|---|
| Provider | **Fly.io** | Same stack, CLI and deploy path as the existing backend; ADR 0003 already chose it |
| Region | **syd** | Same as staging; the phone testing it is in Australia |
| App name | **`forey-test-api`** | Distinct from `sitetracker-backend-staging`; the name is in `backend/fly.test.toml`, so a deploy cannot reach the real app by accident |
| Machine | **shared-cpu-1x, 512 MB**, `min_machines_running = 1`, `auto_stop_machines = off` | Mirrors staging's VM block. It must answer a phone on cellular with the laptop off, and one machine has to keep the evidence volume mounted |
| Database | **`forey-test-db`** — a Fly **unmanaged** Postgres app: `shared-cpu-1x`, 256 MB, one 1 GB volume, region `syd` | One command, the same product the existing staging database was created with, and billed at machine + volume rates. See the limits below |
| Attachment storage | **local adapter on a 1 GB Fly volume** `forey_test_evidence` mounted at `/data/evidence` (`EVIDENCE_STORAGE_BACKEND=local`, `EVIDENCE_LOCAL_ROOT=/data/evidence`) | A machine's own disk is not persistent; a volume is. **Deliberately not Tigris** — see the note below |
| `APP_ENV` | **`test`** | The loader treats `test` as a NON-development environment: a real JWT secret (>= 32 chars, no placeholders) and no wildcard CORS are enforced. It is also the only value that permits the local storage adapter — `staging` and `production` force `s3` (`backend/app/config.py`) |
| `JWT_SECRET` | **generated fresh for this app** | Never the development placeholder, never a secret from the real environment |
| `CORS_ALLOWED_ORIGINS` | `https://forey-test-api.fly.dev` | A wildcard is rejected outside development, and a native app needs no browser origin |
| Code version | **the accepted PR #19 head** | `main` and staging do not have the Site Log API at all |
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
| Postgres machine, shared-cpu-1x 256 MB, always on | ~$1.94 / mo | 1.94 |
| Postgres volume, 1 GB | $0.15 / GB / mo | 0.15 |
| Egress | first 100 GB included | ~0 |
| **Total, left running** | | **~5.43 / month** |

Basis: Fly.io published list pricing for shared-cpu-1x machines and volume
storage, read 2026-09-19. **An estimate from list rates, not a reading of
the account** - I have not logged in to it, and providers change prices.
Machines bill per second, so a three-day acceptance run that is then
destroyed costs well under a dollar. Confirm on the Fly dashboard before
approving.

## Cleanup

```bash
flyctl apps destroy forey-test-api
flyctl apps destroy forey-test-db          # an unmanaged Postgres is an app
flyctl volumes list --app forey-test-api   # expect: no volumes; they go with the app
flyctl apps list                           # expect: sitetracker-backend-staging still present
```

Nothing needs migrating back: the database, the volume and the secrets exist
only for this test.

## Creation, in order (operator; every credential is the operator's)

```bash
cd backend

# 1. The app. --no-deploy so nothing starts before its secrets exist.
flyctl apps create forey-test-api
flyctl volumes create forey_test_evidence --app forey-test-api --region syd --size 1

# 2. The database, and attach it (this sets DATABASE_URL on the app).
#    If this command does not exist in the installed CLI: STOP and report.
flyctl postgres create --name forey-test-db --region syd \
  --vm-size shared-cpu-1x --volume-size 1
flyctl postgres attach forey-test-db --app forey-test-api

# 3. Secrets. Generate the JWT secret; do not reuse one from anywhere.
flyctl secrets set --app forey-test-api \
  APP_ENV=test \
  JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  CORS_ALLOWED_ORIGINS=https://forey-test-api.fly.dev \
  EVIDENCE_STORAGE_BACKEND=local \
  EVIDENCE_LOCAL_ROOT=/data/evidence

# 4. Deploy the accepted PR #19 head, with the TEST config file.
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
