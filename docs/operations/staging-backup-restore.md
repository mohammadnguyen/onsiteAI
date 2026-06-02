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

Rehearsal date: 2026-06-02. Specific evidence values (cluster IDs,
backup ID, dump hash, exact row counts, sanitized log tail) live in the
operator's private ops journal — NOT in this canonical runbook.

| Gate | Status | Evidence reference |
|---|---|---|
| R-1 — native Fly snapshot backup | **VERIFIED** | private ops log: `<BACKUP_ID>` |
| R-2 — portable pg_dump | **VERIFIED** | private ops log: dump sha256 `<SHA256_HASH>` |
| R-3 — disposable MPG cluster create | **VERIFIED** | rehearsal 2026-06-02; private ops log |
| R-4 — pg_restore (Path A, portable dump) | **COMPLETED WITH WARNINGS / pg_restore exit 1** | system-object errors only; private ops log (sanitized tail) |
| R-4 — Path B (native fly mpg restore) | **NOT EXERCISED** (deliberately avoided) | finding 1 below |
| R-5 — verification | **QUALIFIED PASS** | alembic head present + core-table counts plausible; private ops log |
| R-6 — destroy disposable cluster | **CONFIRMED / scheduled teardown** | private ops log |
| R-7 — doc close-out | recorded by this commit | this document |

**Overall: restore rehearsal completed with QUALIFIED PASS** (Path A,
portable dump). This is NOT a clean, zero-error restore: `pg_restore`
exited 1 with system-object (pgbouncer / extension) errors only, and
exact dump-time-count equality was not asserted (no R-2 dump-time
counts were captured). Native restore (Path B) was not exercised.

R-1 and R-2 prove backup artefacts can be CREATED. The 2026-06-02
rehearsal additionally proved the R-2 portable dump is RESTORABLE to a
fresh cluster (R-3 → R-4 → R-5) at the QUALIFIED-PASS level, and that
the disposable cluster was destroyed (R-6). Restore confidence for the
portable-dump path is now established; the native-snapshot restore path
(Path B) remains unproven.

### Rehearsal findings (2026-06-02)

1. Native `fly mpg restore` (Path B) remains deliberately avoided due to
   ambiguous source/target semantics; it was not exercised. Path A
   (portable `.dump` + Docker `pg_restore`) is the exercised path and
   reached a QUALIFIED PASS.
2. The portable `.dump` + Docker `postgres:16` `pg_restore` path is
   viable end-to-end.
3. In this rehearsal, `pg_restore` emitted system-object / pgbouncer /
   extension-related errors and exited 1. Future rehearsals may see
   similar system-object errors.
4. A non-zero `pg_restore` exit must NOT be treated as a pass
   automatically — classify the errors first (see Gate R-4).
5. A QUALIFIED PASS is acceptable only when the `public` schema and core
   tables/data restored, the `alembic_version` row exists, verification
   counts are plausible, and NO data-level error (failed `COPY`, missing
   table, `public.*` constraint/FK failure) occurred.
6. Future rehearsals should capture R-2 dump-time row counts before
   restore, so R-5 can assert exact equality instead of a qualified
   pass.
7. If no native `psql` is on PATH, verify via Docker `postgres:16`
   `psql` through the disposable proxy (see Gate R-5).
8. Do NOT run `fly mpg status <ID> --json` during the rehearsal — it can
   expose a plaintext credentials block. Use `fly mpg list` for status.
9. Gate R-6 destroy is mandatory — especially if the disposable
   cluster's credentials were exposed at any point.

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
- Never run `fly mpg status <ID> --json`. It returns a `credentials`
  block containing the plaintext password and pgbouncer URI. For
  cluster status use `fly mpg list`. (`fly mpg backup list <CLUSTER_ID>
  --json` in Gate R-1 is fine — it carries no credentials.)

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
- Dump-time row counts for the core tables (`jobs`, `expenses`,
  `users`, `suppliers`, plus any other core tables). Capturing these at
  dump time lets Gate R-5 assert EXACT equality against the restored
  cluster instead of settling for a qualified pass. (The 2026-06-02
  rehearsal skipped this, so R-5 reached only a QUALIFIED PASS.)
- Any warnings / errors verbatim (sanitized; no secrets).

## Gate R-3: Create a disposable Fly MPG cluster

**APPROVAL REQUIRED** before running the command. Creates a billable
resource that MUST be destroyed at the end of the rehearsal (Gate
R-6).

**SYNTAX confirmed 2026-06-02** (`fly mpg create --name <NAME> --plan
<PLAN>`). Per ADR 0003, still re-run `fly mpg create --help` before
execution to confirm the current flag shape. The legacy `flyctl
postgres create` command does NOT apply to Fly Managed Postgres.

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

**TWO RESTORE PATHS.** Path A has been exercised to a QUALIFIED PASS.
Path B remains unexercised and should NOT be used until separately
tested. Per ADR 0003, re-run the relevant `--help` before execution.

### Path A — restore from the R-2 portable `.dump`

Uses the local `.dump` produced in R-2. Demonstrates portability (any
Postgres can restore the file).

```powershell
# Terminal A
fly mpg proxy <DISPOSABLE_CLUSTER_ID>

# Terminal B (PowerShell, paste AS ONE BLOCK; mirrors R-2 secret handling + guards)
docker run --rm postgres:16 pg_isready --host=host.docker.internal --port=16380
$readyExit = $LASTEXITCODE
Write-Host "pg_isready exit code: $readyExit"
if ($readyExit -ne 0) {
  throw "pg_isready failed with exit code $readyExit. The proxy is not reachable — do NOT enter the database password until this passes."
}

$secure = Read-Host -AsSecureString "PGPASSWORD for disposable cluster"
$bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:PGPASSWORD = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

docker run --rm `
  -e PGPASSWORD `
  -v "C:/sitetracker_backups:/dump:ro" `
  postgres:16 `
  pg_restore `
    --host=host.docker.internal `
    --port=16380 `
    --username=<DISPOSABLE_DB_USER> `
    --dbname=<DISPOSABLE_DB_NAME> `
    --no-owner --no-acl --verbose `
    /dump/sitetracker_staging_<UTC_TIMESTAMP>_<CONTEXT_TAG>.dump

$code = $LASTEXITCODE
Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
"pg_restore exit code: $code"
"PGPASSWORD still set? " + [bool]$env:PGPASSWORD

if ($code -ne 0) {
  throw "pg_restore exited non-zero ($code). STOP before R-5. Classify the sanitized tail: data-level error => run Gate R-6 destroy; system-object-only errors => continue only if the Gate R-4 qualified-pass checklist is satisfied."
}
```

**Paste AS ONE BLOCK.** As in R-2, `throw` halts only the current
pipeline, so pasting line-by-line could reach the password prompt after
`pg_isready` failed, or flow into R-5 after a failed restore. Select the
whole block and paste once. The dump is mounted read-only (`:ro`) so the
restore container cannot alter the artefact.

The final `throw` is intentional: it stops the block so a non-zero
`pg_restore` exit cannot silently flow into R-5. "Classify sanitized
tail" means apply *Interpreting the `pg_restore` exit code (Path A)*
below — a system-object-only exit 1 is a QUALIFIED PASS (then proceed to
R-5 deliberately); only a data-level error makes the R-6 destroy named
in the throw mandatory.

### Path B — restore from the R-1 native Fly snapshot

Uses Fly's native restore mechanism. Faster, no Docker, no local file
needed. Tests the cloud-native recovery path.

**SYNTAX UNVERIFIED — preflight required.** Run
`fly mpg restore --help` before execution. Likely shape:

```
fly mpg restore <DISPOSABLE_CLUSTER_ID> --backup-id <BACKUP_ID>
```

### Recommendation

Path A (portable `.dump` + Docker `pg_restore`) is the exercised path
and reached a QUALIFIED PASS — the 2026-06-02 rehearsal ran it
end-to-end. This was not a clean or exact restore (see the exit-code
interpretation below); prefer Path A because it is the path that has
actually been run, not because it restored cleanly.

Path B (native `fly mpg restore`) was deliberately NOT exercised. Its
source/target semantics are ambiguous (which backup restores into
which cluster, and whether it can target a fresh disposable cluster
versus only restoring in place), and getting that wrong risks touching
the wrong cluster. Until those semantics are confirmed against current
`fly mpg restore --help` on a throwaway target, treat Path B as
unproven and do not reach for it in an emergency.

### Interpreting the `pg_restore` exit code (Path A)

`pg_restore` exit 1 means "completed with errors that were ignored,"
NOT "aborted." A Fly MPG target pre-provisions system objects (the
`pgbouncer` schema, the `pg_stat_monitor` and `pgaudit` extensions),
so restoring a dump that also references them produces a handful of
benign system-object errors. In this rehearsal `pg_restore` emitted
errors of the form:

- `schema "pgbouncer" already exists`
- `must be owner of extension pg_stat_monitor`
- `must be owner of extension pgaudit`
- `permission denied for schema pgbouncer`

and exited 1. Future rehearsals may see similar system-object errors.

A non-zero exit is a QUALIFIED PASS — not an automatic pass — only
when ALL of the following hold:

1. Every error is a system-object error (pgbouncer schema, extension
   ownership, or pgbouncer-schema permission), NOT a `public.*` object.
2. No `COPY` failed and no table's data load errored.
3. The `public` schema, its tables, constraints, indexes and FKs were
   all created (visible in the `--verbose` log).
4. Gate R-5 verification then finds the `alembic_version` row and
   plausible core-table counts.

If ANY error references a `public.*` object, a failed `COPY`, a
missing table, or a constraint/FK that did not build: STOP. That is a
data-level failure, not a benign warning. Do not proceed to trust the
backup.

### Optional: cleaner exit via public-schema-only restore

To avoid the system-object noise entirely, a restore can be scoped to
just the application schema by adding `--schema=public` to the
`pg_restore` invocation above. It typically exits 0 because it never
touches the pre-provisioned pgbouncer schema or the extensions.
Caveat: it restores ONLY the `public` schema, so anything the
application legitimately places outside `public` would be skipped. The
full-dump restore (no `--schema` filter) remains the canonical
rehearsal path because it exercises the whole artefact; the
`--schema=public` variant is a diagnostic convenience, not the
default.

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

If `fly mpg connect` cannot find a native `psql` on PATH, run the same
queries through Docker `postgres:16` against the disposable proxy (same
secret hygiene as R-2 — `Read-Host -AsSecureString`, `-e PGPASSWORD`
pass-through, wipe after):

```powershell
# Terminal A: fly mpg proxy <DISPOSABLE_CLUSTER_ID>  (still up from R-4)

# Terminal B (PowerShell, paste as one block)
$secure = Read-Host -AsSecureString "PGPASSWORD for disposable cluster"
$bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:PGPASSWORD = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

docker run --rm `
  -e PGPASSWORD `
  postgres:16 `
  psql `
    -v ON_ERROR_STOP=1 `
    --host=host.docker.internal `
    --port=16380 `
    --username=<DISPOSABLE_DB_USER> `
    --dbname=<DISPOSABLE_DB_NAME> `
    -c "SELECT version_num FROM alembic_version;" `
    -c "SELECT COUNT(*) FROM jobs;" `
    -c "SELECT COUNT(*) FROM expenses;" `
    -c "SELECT COUNT(*) FROM users;" `
    -c "SELECT COUNT(*) FROM suppliers;"

Remove-Item env:PGPASSWORD
"PGPASSWORD set? $([bool]$env:PGPASSWORD)"
```

### Pass criteria

**Exact pass** — every restored count equals the source count. This
requires EITHER the R-2 dump-time counts (captured per R-2 "Evidence
to capture") OR a live cross-check against the staging cluster at
verification time together with confidence that the live data did not
change between dump and check. Only then can equality be asserted
exactly.

**Qualified pass** — the `alembic_version` row is present, the
core-table counts are plausible (non-zero where data is expected), and
Gate R-4 reported NO data-level error (no failed `COPY`, no missing
`public.*` object). This is the ceiling the 2026-06-02 rehearsal
reached, because R-2 dump-time counts were not captured.

**If a data-level mismatch appears** (a `public.*` table missing, or a
count that contradicts a known-good source value): STOP. The backup is
not faithfully restorable. Do NOT trust production data on this backup
path until the discrepancy is resolved. Report and pause.

## Gate R-6: Destroy the disposable cluster

**APPROVAL REQUIRED** before running the command.

**SYNTAX confirmed 2026-06-02** (`fly mpg destroy
<DISPOSABLE_CLUSTER_ID>`, interactive — prompts for confirmation). Per
ADR 0003, still re-run `fly mpg destroy --help` before execution to
confirm the current flag shape. Shape:

```
fly mpg destroy <DISPOSABLE_CLUSTER_ID>
```

Frees the cluster's resources and stops billing. Gate R-6 is mandatory
and is doubly urgent if the disposable cluster's credentials were
exposed at any point during the rehearsal (e.g. surfaced in a
transcript). Run it interactively (no `--yes`) and confirm the prompt
shows the DISPOSABLE cluster's ID before accepting — never destroy by
reflex.

**Post-step verification.** `fly mpg list --org <ORG_SLUG>` no longer
shows the disposable cluster.

## Gate R-7: Close-out

Report (private ops journal, NOT this repo):

- Backup ID created in R-1 (`<BACKUP_ID>` reference).
- Local pg_dump file path + size + SHA-256 hash (`<SHA256_HASH>`
  reference).
- Counts from R-5 (and, if dump-time counts were captured in R-2, the
  exact comparison; otherwise note the qualified-pass basis).
- Confirmation that the disposable cluster was destroyed (R-6).
- Total rehearsal duration (for future planning estimates).
- Record the `## Verification status` outcome in a separate doc-only
  commit using accurate statuses — R-3 **VERIFIED**, R-4 **COMPLETED
  WITH WARNINGS / pg_restore exit 1**, R-5 **QUALIFIED PASS**, R-6
  **CONFIRMED / scheduled teardown** — NOT a blanket "VERIFIED". The
  rehearsal date may appear inline; specific evidence values must not.
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
