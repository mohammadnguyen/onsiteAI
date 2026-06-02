# Staging backup + restore rehearsal

Procedural reference for the rehearsal that proves the staging Postgres
backup is actually restorable before any production data is trusted.

Per [ADR 0003](../adr/0003-staging-deployment-strategy.md): backup =
Fly Managed Postgres provider-managed snapshots + manual `pg_dump`
fallback. Restore is manual for V1.

Per ADR 0003 §"Runbook Authoring Principles": the commands shown below
are DATED EXAMPLES, not canonical truth. Before any stateful gate the
operator re-runs the relevant `<command> --help` to confirm the example
matches the current CLI. A mismatch between the runbook example and
current `--help` blocks the gate until the runbook is amended.

## Target environment (template)

This runbook targets a Fly Managed Postgres cluster. The operator
substitutes project-specific values before execution. Specific values
(cluster ID, backup ID, dump hash, etc.) belong in the operator's
private ops journal — NOT in this canonical runbook.

| Field | Placeholder | How to obtain |
|---|---|---|
| Cluster ID | `<CLUSTER_ID>` | `fly mpg list --org <ORG_SLUG>` |
| Cluster name | `<CLUSTER_NAME>` | same |
| Region | `<REGION>` | same |
| Plan tier | `<PLAN>` | same |
| Database name | `<DB_NAME>` | `fly mpg databases list <CLUSTER_ID>` |
| Database user | `<DB_USER>` | `fly mpg users list <CLUSTER_ID>` |
| Attached backend app | `<BACKEND_APP_NAME>` | the `[app]` line in `backend/fly.toml` |
| Password | (operator's private records OR controlled `DATABASE_URL` extraction) | **NEVER documented here** |

## Verification status (current installation)

| Phase | Status | Evidence reference |
|---|---|---|
| Backup creation (R-1, native Fly snapshot) | **VERIFIED** | private ops log: `<BACKUP_ID>` |
| Backup creation (R-2, portable pg_dump) | **VERIFIED** | private ops log: dump file sha256 `<SHA256_HASH>` |
| Restore (R-3 + R-4 + R-5) | **NOT YET VERIFIED** | Rehearsal pending |
| Disposable cluster cleanup (R-6) | **NOT YET VERIFIED** | Pending R-3 execution |

R-1 and R-2 prove that backup artefacts can be CREATED. They do NOT
prove that those artefacts can be RESTORED. The backup strategy is not
fully proven until the restore loop (R-3 → R-4 → R-5) passes. Until
then, defense-in-depth backup artefacts exist but restore confidence
is incomplete.

## Prerequisites

- Fly Managed Postgres cluster exists and is attached to the backend
  app via `fly mpg attach <CLUSTER_ID>`.
- `fly` CLI installed and authenticated (`fly auth whoami` returns the
  operator's account).
- Docker Desktop installed and running in Linux containers mode.
- `postgres:16` image pulled locally (one-time):
  `docker pull postgres:16`.
- `pg_dump` verified accessible via Docker (one-time):
  `docker run --rm postgres:16 pg_dump --version`.
- Local dump folder OUTSIDE the repo AND OUTSIDE any cloud-synced
  directory. Recommended on Windows: `C:\sitetracker_backups\`.
  Avoid `Documents` if it has been redirected into OneDrive.

## Gate R-1: Trigger a Fly MPG backup manually

**APPROVAL REQUIRED** before running the commands.

```
fly mpg backup create <CLUSTER_ID>
fly mpg backup list <CLUSTER_ID> --json
```

The operator records the new backup ID in the private ops journal.
Fly Managed Postgres also runs an automatic backup schedule; this
manual backup is the rehearsal target.

The `backup create` command returns the backup ID instantly. The
snapshot itself runs asynchronously server-side. Re-run
`fly mpg backup list <CLUSTER_ID> --json` after ~60-90 seconds and
confirm the new backup record shows `status: completed` with real
`start` and `stop` timestamps. A backup with `status: pending` is
registered but not yet committed; do not treat it as complete until
the status flips.

**Post-step verification.** `fly mpg backup list <CLUSTER_ID> --json`
shows the new backup ID at the top with `status: completed` and
non-empty `start` / `stop`.

## Gate R-2: Capture pg_dump fallback to local disk

**APPROVAL REQUIRED** before running the commands.

Fly Managed Postgres has no SSH console path. The MPG-correct flow
uses two terminals: `fly mpg proxy` forwards a local TCP port to the
cluster, and a local Docker `postgres:16` container runs `pg_dump`
against that port.

### Terminal A — start the proxy

```
fly mpg proxy <CLUSTER_ID>
```

Blocks foreground; listens on `127.0.0.1:16380` by default. Leave the
window open until Terminal B completes.

### Terminal B — three-guard pg_dump (PowerShell, paste AS ONE BLOCK)

```powershell
New-Item -ItemType Directory -Force C:\sitetracker_backups

docker run --rm postgres:16 pg_isready -h host.docker.internal -p 16380

$readyExit = $LASTEXITCODE
Write-Host "pg_isready exit code: $readyExit"
if ($readyExit -ne 0) {
  throw "pg_isready failed with exit code $readyExit"
}

$secure = Read-Host -AsSecureString "PGPASSWORD for staging dump"
$bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:PGPASSWORD = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
Write-Host "Dump start UTC: $ts"

docker run --rm `
  -e PGPASSWORD `
  -v "C:/sitetracker_backups:/dump" `
  postgres:16 `
  pg_dump `
    --host=host.docker.internal `
    --port=16380 `
    --username=<DB_USER> `
    --dbname=<DB_NAME> `
    --format=custom `
    --no-owner `
    --no-acl `
    --file=/dump/sitetracker_staging_${ts}_<CONTEXT_TAG>.dump

$dumpExit = $LASTEXITCODE
Write-Host "pg_dump exit code: $dumpExit"
if ($dumpExit -ne 0) {
  Remove-Item env:PGPASSWORD -ErrorAction SilentlyContinue
  throw "pg_dump failed with exit code $dumpExit"
}

Remove-Item env:PGPASSWORD

$path = "C:\sitetracker_backups\sitetracker_staging_${ts}_<CONTEXT_TAG>.dump"
Get-Item $path | Select-Object FullName, Length, LastWriteTimeUtc
Get-FileHash $path -Algorithm SHA256
"PGPASSWORD set? $([bool]$env:PGPASSWORD)"
```

**Paste-as-one-block reason.** PowerShell `throw` halts only the
current pipeline. Pasting line-by-line lets execution continue past a
fired guard (e.g. you could end up at the password prompt even after
`pg_isready` failed). Always select the entire Terminal B block and
paste at once so the guards take effect.

### Terminal A — stop the proxy

After Terminal B completes successfully, Ctrl-C in Terminal A.

### pg_dump flag rationale

| Flag | Why |
|---|---|
| `--format=custom` | Compressed binary; restorable with `pg_restore`; standard for Postgres backups |
| `--no-owner` | Strips role-specific `OWNER` statements; portable to any restore target |
| `--no-acl` | Strips `GRANT` / `REVOKE`; portable |
| NOT `--clean` / `--if-exists` | Restore-target-aware flags belong at restore time, not dump time. Adding them here would generate `DROP IF EXISTS` statements that increase the blast radius if a dump is ever accidentally restored against the wrong target. |
| `-e PGPASSWORD` (no `=value`) | Env-var pass-through from parent shell; the password never lands on the command line. |

### Secret handling discipline

- `DATABASE_URL` and the database password NEVER enter chat,
  documentation, commit messages, evidence files, or AI assistant
  transcripts.
- Use `Read-Host -AsSecureString` in PowerShell (no echo, no command
  history).
- Inject into Docker via `-e PGPASSWORD` only (env-var pass-through).
  Never use `--password=...` on the `pg_dump` command line (visible
  in process listings and shell history).
- Wipe immediately after dump: `Remove-Item env:PGPASSWORD`.
- Verify wipe: `"PGPASSWORD set? $([bool]$env:PGPASSWORD)"` must
  return `False` before closing the terminal.
- Never paste `DATABASE_URL`, password, connection string, or
  `.dump`-file contents into evidence.

### Post-step verification

- `pg_isready` exit code 0.
- `pg_dump` exit code 0.
- `$env:PGPASSWORD` confirmed `False` after `Remove-Item`.
- Dump file exists at the expected path with non-zero size.
- SHA-256 hash captured into the private ops journal.
- `git status --porcelain` in both worktrees shows the dump file is
  NOT inside the repo (the `C:\sitetracker_backups\` path is outside
  any git-tracked directory, so this is structurally guaranteed).

### Evidence to capture (private ops journal, NOT this repo)

- UTC start + end timestamps (`<UTC_TIMESTAMP>`).
- Cluster ID, database name, username.
- Dump file path, size, SHA-256 hash (`<SHA256_HASH>`).
- `pg_dump` exit code.
- Backup context tag (`<CONTEXT_TAG>` portion of the filename).
- `PGPASSWORD` wipe confirmation.
- Any warnings / errors verbatim (sanitized; no secrets).

## Gate R-3: Create a disposable Fly MPG cluster

**APPROVAL REQUIRED** before running the command. Creates a billable
resource that MUST be destroyed at the end of the rehearsal (Gate
R-6).

**SYNTAX UNVERIFIED — preflight required.** Before this gate executes,
run `fly mpg create --help` to confirm the current flag shape. The
legacy `flyctl postgres create` command does NOT apply to Fly Managed
Postgres.

Likely current shape (verify before use):

```
fly mpg create \
  --name <DISPOSABLE_CLUSTER_NAME> \
  --region <REGION> \
  --plan <SMALLEST_AVAILABLE_PLAN>
```

The disposable cluster's plan tier should be the smallest Fly MPG
plan that can hold the rehearsal restore. The cluster is destroyed
at R-6.

**Post-step verification.** `fly mpg list --org <ORG_SLUG>` shows the
new disposable cluster.

## Gate R-4: Restore to the disposable cluster

**APPROVAL REQUIRED** before running the commands.

**TWO RESTORE PATHS** (operator chooses; both unverified, preflight
required before execution).

### Path A — restore from the R-2 portable `.dump`

Uses the local `.dump` produced in R-2. Demonstrates portability (any
Postgres can restore the file).

```powershell
# Terminal A
fly mpg proxy <DISPOSABLE_CLUSTER_ID>

# Terminal B (PowerShell, paste as one block; mirrors R-2 secret handling)
$secure = Read-Host -AsSecureString "PGPASSWORD for disposable cluster"
$bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:PGPASSWORD = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

docker run --rm `
  -e PGPASSWORD `
  -v "C:/sitetracker_backups:/dump" `
  postgres:16 `
  pg_restore `
    --host=host.docker.internal `
    --port=16380 `
    --username=<DISPOSABLE_DB_USER> `
    --dbname=<DISPOSABLE_DB_NAME> `
    --no-owner --no-acl --verbose `
    /dump/sitetracker_staging_<UTC_TIMESTAMP>_<CONTEXT_TAG>.dump

Remove-Item env:PGPASSWORD
```

### Path B — restore from the R-1 native Fly snapshot

Uses Fly's native restore mechanism. Faster, no Docker, no local file
needed. Tests the cloud-native recovery path.

**SYNTAX UNVERIFIED — preflight required.** Run
`fly mpg restore --help` before execution. Likely shape:

```
fly mpg restore <DISPOSABLE_CLUSTER_ID> --backup-id <BACKUP_ID>
```

### Recommendation

If practical, run BOTH paths (cost: extra ~10 minutes; benefit:
proves both restore mechanisms independently). If only one, prefer
Path B (native; faster; matches the primary backup mechanism the
operator would reach for in an emergency).

**Post-step verification.** See Gate R-5.

## Gate R-5: Verification queries

**APPROVAL REQUIRED** before running the queries (read-only on the
disposable cluster).

```
fly mpg connect <DISPOSABLE_CLUSTER_ID>
SELECT version_num FROM alembic_version;
SELECT COUNT(*) FROM jobs;
SELECT COUNT(*) FROM expenses;
SELECT COUNT(*) FROM users;
\q
```

Compare each count to the same query run against the live staging
cluster (`fly mpg connect <CLUSTER_ID>`). All counts must match for
the rehearsal to pass.

**If counts do NOT match:** STOP. The backup is not faithfully
restorable. Do NOT trust production data on this backup path until
the discrepancy is resolved. Report and pause.

## Gate R-6: Destroy the disposable cluster

**APPROVAL REQUIRED** before running the command.

**SYNTAX UNVERIFIED — preflight required.** Run
`fly mpg destroy --help` (or whichever subcommand `fly mpg --help`
shows for cluster deletion) before execution. Likely shape:

```
fly mpg destroy <DISPOSABLE_CLUSTER_ID>
```

Frees the cluster's resources and stops billing.

**Post-step verification.** `fly mpg list --org <ORG_SLUG>` no longer
shows the disposable cluster.

## Gate R-7: Close-out

Report (private ops journal, NOT this repo):

- Backup ID created in R-1 (`<BACKUP_ID>` reference).
- Local pg_dump file path + size + SHA-256 hash (`<SHA256_HASH>`
  reference).
- Counts comparison from R-5 (live staging vs disposable, all
  matching).
- Confirmation that the disposable cluster was destroyed (R-6).
- Total rehearsal duration (for future planning estimates).
- Flip the `## Verification status` table entries for R-3 / R-4 /
  R-5 / R-6 from **NOT YET VERIFIED** → **VERIFIED** in a separate
  doc-only commit.
- All sanitized evidence captured in the operator's private ops
  journal (path: operator's choice; explicitly NOT in this repo).

## What NOT to overwrite

- **DO NOT** restore the pg_dump or trigger `fly mpg restore` into
  the live staging cluster. That would overwrite real staging data.
  Use only the disposable cluster created in R-3.
- **DO NOT** delete the local `.dump` file until restorability is
  confirmed (Gate R-5 passed).
- **DO NOT** run destructive SQL (`DROP DATABASE`, `TRUNCATE`, etc.)
  against any cluster other than the disposable rehearsal cluster,
  and even there only for restore-prep.
- **DO NOT** skip Gate R-6 (destroy disposable cluster) — leaving it
  running incurs ongoing Fly MPG charges.

## Hard boundaries

- No production data is involved in this rehearsal (staging only).
- The rehearsal does NOT modify the live staging cluster (only reads
  from it for the snapshot + the count comparison).
- Each gate requires explicit operator approval BEFORE the command
  runs.
- If any verification fails, STOP and report — do not auto-continue.

## Legacy commands — DO NOT USE for Fly Managed Postgres

The commands below target the legacy unmanaged `flyctl postgres`
surface and DO NOT apply to Fly Managed Postgres. They are listed
here only so anyone returning to this runbook can recognise stale
examples in their search history or in older runbook revisions.

| Legacy (unmanaged Postgres) — DO NOT USE for MPG | Current (MPG) — USE |
|---|---|
| `flyctl postgres backup create --app <name>` | `fly mpg backup create <CLUSTER_ID>` |
| `flyctl postgres backup list --app <name>` | `fly mpg backup list <CLUSTER_ID> --json` |
| `flyctl ssh console --app <pg-app-name>` | No equivalent for MPG (managed service has no shell). Use `fly mpg connect <CLUSTER_ID>` for `psql` OR `fly mpg proxy <CLUSTER_ID>` for local-tool access. |
| `flyctl postgres create --name ...` | `fly mpg create --name ... --plan <PLAN>` (verify syntax) |
| `flyctl postgres connect --app <name>` | `fly mpg connect <CLUSTER_ID>` |
| `flyctl postgres destroy --app <name>` | `fly mpg destroy <CLUSTER_ID>` (verify syntax) |
| In-container `pg_dump` via SSH | `fly mpg proxy <CLUSTER_ID>` + local Docker `pg_dump` against `host.docker.internal:16380` |

If any future runbook revision reintroduces a `flyctl postgres ...`
command against Fly Managed Postgres, the runbook is regressing and
must be corrected.
