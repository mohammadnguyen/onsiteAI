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

## Gate D-2: Region placement (deprecated as an active gate)

**No action required under modern Fly Machines.** This gate was
originally documented to run:

```
flyctl regions set syd --app sitetracker-backend-staging
```

That command is **deprecated by Fly** (`fly regions set` returns
"This command is no longer supported; use fly scale count to scale
the number of Machines in a region"). Region placement is now
governed by:

```
backend/fly.toml: primary_region = "syd"
```

`flyctl deploy` (Gate D-6) reads `primary_region` and places the new
machine(s) accordingly. No separate `regions set` step exists or is
required.

**Verification.** Region placement is verified AFTER Gate D-6 deploy
via `fly status --app sitetracker-backend-staging`. The output's
`Region` column for each machine must read `syd`.

**Corrective action (separate approved gate, NOT bundled with D-2 or
D-6).** If a deployed machine lands outside `syd`, the operator may
execute:

```
fly scale count 1 --region syd --app sitetracker-backend-staging
```

as its own separately-approved corrective gate. Bundling this
corrective into D-2 or D-6 would violate the single-gate governance
model. It is a recovery action only, triggered by a post-D-6
verification failure.

Per ADR 0003: Sydney is primary. If `syd` is unavailable for Fly
compute or Postgres at the next D-3 / D-6 attempt (capacity-
constrained or marked preview), STOP — fall back per ADR 0003's
Render Singapore alternative and amend ADR 0003 in a separate batch.

## Gate D-3: Provision the staging PostgreSQL cluster

**APPROVAL REQUIRED** before execution. Creates a billable resource.

### Intent

Create a single-node, non-HA PostgreSQL cluster in Sydney to back the
staging environment. Cluster naming follows the `-staging` suffix
convention so the environment is self-describing. Major version
matches local development. Plan tier is the lowest non-HA, single-
node tier available — this gate is for verifying deploy/runtime
workflow, not high availability, replica sync, or DB scaling.

### Invariants this gate must preserve

- Cluster lives in the same Fly organization as the backend app.
- Cluster lives in `syd` to minimize latency to the backend app.
- Cluster is dedicated to staging — no production data shares it.
  The `-staging` name suffix enforces this self-description.
- PostgreSQL major version is 16 (matches `backend/docker-compose.yml`
  and the alembic migrations developed against local dev).
- Cluster has no public network exposure — reachable only via Fly's
  internal network or `fly mpg connect` / `fly mpg proxy`.
- No connection string, password, or pooled-endpoint URL printed by
  `fly mpg create` enters chat, commit messages, documentation, or
  any non-quarantined file.

### Expected side effects

- One new managed Postgres cluster appears in the operator's Fly
  organization, billed at the selected plan tier from creation onward.
- A cluster ID is printed once on success — operator records this ID
  for Gate D-4 (attach) and cross-checks it via `fly mpg list --org
  personal`.
- No app changes; no secrets set on the backend app yet.
- Fly may provision provider-managed backups automatically depending
  on the chosen plan tier (operator verifies via post-step).
- `fly mpg create` prints a working `postgresql://...` connection
  string with the cluster's `fly-user` pooled-endpoint password to
  stdout. Apply the Credential safety pattern below BEFORE running
  the command.

### Credential safety

`fly mpg create` prints a usable `postgresql://fly-user:<password>@...`
connection string to stdout on success. Once that string lands in a
shell history, a chat transcript, a CI log, or a screenshot, the
cluster's pooled-endpoint user is considered compromised.

Required at execution time:

1. **Redirect raw output to a non-repo file.** The redirect target
   lives under the operator's home directory (e.g.
   `$env:USERPROFILE\Documents\sitetracker_backups\fly_mpg_create_<ts>.txt`).
   It does NOT live in the repository working tree.
2. **Never paste raw create output into chat, commit messages, or
   documentation.** Treat the redirect file as a one-time capture,
   not a long-term artefact.
3. **Report only enumerated safe fields**: cluster ID, name,
   organization, region, status, plan tier, attached apps (must be
   empty post-create), allocated disk size, replica count, Postgres
   major version. Do NOT report: connection string, pooled-endpoint
   host, password, direct IP.
4. **Do NOT inspect or report password-bearing lines from the sink
   file.** Verify cluster state through `fly mpg list` / `fly mpg
   status` / `flyctl secrets list`, not by pasting sink output. The
   cluster ID is the only field that may be extracted from the sink
   file, and even that must be cross-checked against `fly mpg list
   --org personal`. In practice, reading the cluster ID directly
   from `fly mpg list --org personal` avoids opening the sink file
   at all.
5. **Delete or quarantine the local sink file after extracting the
   cluster ID; do not rely on file deletion as credential rotation.**
   Windows OneDrive, SSD wear-levelling, backup snapshots, and shell
   history may all retain copies of the captured output regardless
   of local file deletion. If the credential reached anywhere
   outside the local terminal session, treat the cluster as
   compromised regardless of whether the sink file is deleted.

### Contamination rule

If a connection string or password reaches chat, the transcript, the
repository, or any non-quarantined file:

- The cluster is **compromised** until its pooled-endpoint user is
  rotated.
- If the cluster has zero application data, zero schema, zero
  secrets on the attached app, and zero attached apps, the cleanest
  remediation is **destroy and redo**: open a separate D-3R destroy
  gate, await async teardown completion (cluster fully absent from
  `fly mpg list --org personal`), then re-open D-3 with a fresh
  attempt under the Credential safety pattern above.
- If the cluster has data, schema, secrets, or attached apps, stop
  and open a separate credential-rotation remediation plan before
  proceeding. Destroy-and-redo is too expensive once application
  state exists; the remediation path requires its own planning
  round and is out of scope for D-3 prose.

### Rollback

If the cluster is created in the wrong region, wrong org, or with
the wrong plan tier, destroy it as a SEPARATE single gate before
proceeding to D-4. Do not attach a wrongly-provisioned cluster. The
destroy command is `fly mpg destroy <CLUSTER_ID>` and requires its
own APPROVAL REQUIRED gate (not bundled with D-3 or D-4).

### Verification (pre-create)

Before issuing the create command, confirm a clean slate:

- `fly mpg list --org personal` shows no cluster named
  `sitetracker-pg-staging` in any status. If a prior aborted attempt
  left the name in `deleting` status, wait for full async teardown
  before re-creating (Fly may take a few minutes; the empty-org
  sentinel `"No managed postgres clusters found in organization
  personal"` is the green signal).
- `flyctl secrets list --app sitetracker-backend-staging` shows no
  `DATABASE_URL` secret. The backend app must be in attach-ready
  state — not already wired to a different DB from a prior attach
  attempt.

### Verification (post-create)

- `fly mpg list --org personal` shows the new cluster with status
  `ready`, in region `syd`, on the agreed plan tier, and with
  `<no attached apps>`.
- `fly mpg status <CLUSTER_ID>` reports the cluster up; replica
  count is 1 (single-node confirmed); allocated disk matches the
  `--volume-size` flag from the create command; Postgres major
  version is 16.
- `flyctl secrets list --app sitetracker-backend-staging` still
  shows no `DATABASE_URL` (D-4 attach has not been performed; that
  is a separate gate).

### Example command (Fly CLI as of 2026-05-19 — confirm `fly mpg create --help` before use)

Per ADR 0003's Runbook Authoring Principles, the block below is
illustrative. The operator re-runs `fly mpg create --help` immediately
before execution and aborts this gate if the flag surface has drifted.
Output is redirected to a non-repo sink per the Credential safety
section above.

```
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$sink = "$env:USERPROFILE\Documents\sitetracker_backups\fly_mpg_create_$ts.txt"
$null = New-Item -ItemType Directory -Force -Path (Split-Path $sink)

fly mpg create `
  --org <org-slug> `
  --region syd `
  --name sitetracker-pg-staging `
  --volume-size 10 `
  --pg-major-version 16 `
  --plan <LOWEST_AVAILABLE_TIER> `
  *> $sink
```

`<org-slug>` is the operator's Fly organization (typically `personal`
for a single-operator account; verify via `fly orgs list`).
`<LOWEST_AVAILABLE_TIER>` is operator-selected from the plan list
printed by `fly mpg create --help` at execution time. Per ADR 0003,
the lowest non-HA, single-node tier is the staging baseline; do not
select a performance or HA tier without amending the ADR.

After the command completes:

1. Read the new cluster's ID from `fly mpg list --org personal`.
   This is the only field D-4 attach requires.
2. Confirm cluster health via `fly mpg status <CLUSTER_ID>`.
3. Delete or quarantine the local sink file. Treat that step as a
   hygiene measure, NOT as credential rotation — see Credential
   safety rule 5.

### References

- ADR 0003 (staging deployment strategy, Runbook Authoring Principles)
- Fly Managed Postgres: https://fly.io/docs/mpg/

## Gate D-4: Attach the staging PostgreSQL cluster to the backend app

**APPROVAL REQUIRED** before execution.

### Intent

Wire the staging Postgres cluster to the staging backend app such
that the app receives a connection string via Fly's secret-injection
mechanism on the next boot. The connection string never enters chat,
the operator's clipboard, or git history — Fly handles it server-
side as an injected secret.

### Invariants this gate must preserve

- Connection string is injected by Fly directly into the backend
  app's secret environment — NEVER set manually with `flyctl secrets
  set DATABASE_URL=...`.
- The injected env variable is named `DATABASE_URL` (what
  `app.config.Settings` reads).
- The backend app receives scoped per-app DB credentials, not
  cluster-admin credentials.
- Cluster and backend app remain in the same Fly org and region.

### Expected side effects

- One new secret named `DATABASE_URL` appears on the backend app's
  secret list. Value is NEVER printed by listing commands.
- The backend app restarts as part of attach (provider behaviour).
  Acceptable at this stage — the app has no production traffic until
  Gate D-6 deploys the image.
- One new DB and one new DB user are provisioned inside the cluster,
  scoped to the backend app.

### Rollback

If the wrong cluster ID is attached, detach via `fly mpg detach` as a
SEPARATE single gate; verify `DATABASE_URL` is removed from the
backend app's secret list before re-attempting D-4 with the correct
cluster ID. Do not attempt to "fix" a wrong attach by overwriting
the secret manually.

### Verification (post-step)

- `flyctl secrets list --app sitetracker-backend-staging` shows
  `DATABASE_URL` as a secret name. Values are NEVER printed by this
  command.
- The cluster's database list (`fly mpg databases list <CLUSTER_ID>`
  or equivalent) shows the new per-app DB.
- The backend app's activity log shows the restart event.

### Example command (Fly CLI as of 2026-05-19 — confirm `fly mpg attach --help` before use)

Per ADR 0003's Runbook Authoring Principles, the block below is
illustrative. The operator re-runs `fly mpg attach --help` immediately
before execution and aborts this gate if the flag surface has drifted.

```
fly mpg attach <CLUSTER_ID> --app sitetracker-backend-staging
```

`<CLUSTER_ID>` is the ID captured from Gate D-3 stdout. Default flags
are sufficient — no `--variable-name`, `--database`, or `--username`
overrides.

### References

- ADR 0003 (DATABASE_URL injection)
- `docs/operations/env-and-secrets.md` § Hosting-provider secret injection

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
`syd`. Additionally, `fly mpg status <CLUSTER_ID>` reports the
attached Postgres cluster up and reachable from the app.

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

**Post-step verification.** `fly mpg connect <CLUSTER_ID>` → `\dt` shows expected tables (`jobs`,
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

**Post-step verification.** `fly mpg connect <CLUSTER_ID>` → `SELECT email, role FROM users WHERE
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
