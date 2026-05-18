# Staging backup + restore rehearsal

Procedural reference for Slice 2-B3 — the mandatory rehearsal that
proves the staging Postgres backup is actually restorable before any
production data is trusted.

Per [ADR 0003](../adr/0003-staging-deployment-strategy.md): backup =
Fly Postgres automatic daily snapshots + manual `pg_dump` fallback.
Restore is manual for V1.

This document is NOT executed by Slice 2-B1 (the doc batch this file
ships in) or by Slice 2-B2 (the staging deploy). It is executed by a
separate Slice 2-B3 batch with its own approval.

## Prerequisites

- Slice 2-B2 completed: staging backend + Fly Postgres exist.
- `flyctl` CLI on the operator's local machine.
- A path on the local disk for the pg_dump fallback file (suggested:
  `C:/Users/User/Documents/sitetracker_backups/` on Windows,
  `~/sitetracker_backups/` on macOS / Linux). Same convention as the
  Half B Gate 0 backup pattern.

## Gate R-1: Trigger a Fly snapshot manually

**APPROVAL REQUIRED** before running the command.

```
flyctl postgres backup create --app sitetracker-pg-staging
flyctl postgres backup list --app sitetracker-pg-staging
```

The operator records the new snapshot ID. Fly Postgres takes
automatic daily snapshots; this manual snapshot is the rehearsal
target.

**Post-step verification.** `flyctl postgres backup list` shows the
new snapshot ID with the current timestamp.

## Gate R-2: Capture pg_dump fallback to local disk

**APPROVAL REQUIRED** before running the command.

```
flyctl ssh console --app sitetracker-pg-staging
# inside the container:
pg_dump -U postgres -d <db_name> > /tmp/staging_dump.sql
exit
flyctl ssh sftp get /tmp/staging_dump.sql \
  ./staging_dump_$(date +%Y%m%d_%H%M%S).sql \
  --app sitetracker-pg-staging
```

The downloaded file goes to the operator's local disk OUTSIDE the
repo.

**Post-step verification.**

- File size > 0.
- Starts with `-- PostgreSQL database dump`.
- Modern pg_dump versions append `\restrict ... \unrestrict`
  directives; the legacy `-- PostgreSQL database dump complete` marker
  is also present near the end of file.

## Gate R-3: Create a disposable Fly Postgres cluster

**APPROVAL REQUIRED** before running the command. Creates a billable
resource that MUST be destroyed at the end of the rehearsal (Gate
R-6).

```
flyctl postgres create \
  --name sitetracker-pg-restore-test \
  --region syd \
  --vm-size shared-cpu-1x \
  --volume-size 5
```

Smaller volume (5 GB) since it only holds the rehearsal restore.

**Post-step verification.** `flyctl postgres list` shows
`sitetracker-pg-restore-test`.

## Gate R-4: Restore the pg_dump to the disposable cluster

**APPROVAL REQUIRED** before running the command.

```
flyctl ssh sftp put ./staging_dump_<timestamp>.sql /tmp/staging_dump.sql \
  --app sitetracker-pg-restore-test
flyctl ssh console --app sitetracker-pg-restore-test
# inside the container:
psql -U postgres -d <db_name> -f /tmp/staging_dump.sql
exit
```

**Post-step verification.** See Gate R-5.

## Gate R-5: Verification queries

**APPROVAL REQUIRED** before running the queries (read-only on the
disposable cluster).

```
flyctl postgres connect --app sitetracker-pg-restore-test
SELECT version_num FROM alembic_version;
SELECT COUNT(*) FROM jobs;
SELECT COUNT(*) FROM expenses;
SELECT COUNT(*) FROM users;
\q
```

Compare each count to the same query run against the staging cluster.
All counts must match for the rehearsal to pass.

**If counts do NOT match:** STOP. The backup is not faithfully
restorable. Do NOT trust production data on this backup path until
the discrepancy is resolved. Report and pause.

## Gate R-6: Destroy the disposable cluster

**APPROVAL REQUIRED** before running the command.

```
flyctl postgres destroy --app sitetracker-pg-restore-test --yes
```

Frees the volume and stops billing on the disposable cluster.

**Post-step verification.** `flyctl postgres list` no longer shows
`sitetracker-pg-restore-test`.

## Gate R-7: Close-out

Report:

- Snapshot ID created in R-1.
- Local pg_dump file path + size.
- Counts comparison from R-5 (staging vs disposable, all matching).
- Confirmation that the disposable cluster was destroyed (R-6).
- Total rehearsal duration (for future planning estimates).

## What NOT to overwrite

- **DO NOT** restore the pg_dump into the live `sitetracker-pg-staging`
  cluster. That would overwrite real staging data. Use only the
  disposable cluster (`sitetracker-pg-restore-test`).
- **DO NOT** delete the local `staging_dump_<timestamp>.sql` until
  restorability is confirmed (Gate R-5 passed).
- **DO NOT** run destructive SQL (`DROP DATABASE`, `TRUNCATE`, etc.)
  against any cluster other than the disposable
  `sitetracker-pg-restore-test`, and even there only for restore-prep.
- **DO NOT** skip Gate R-6 (destroy disposable cluster) — leaving it
  running incurs ongoing Fly Postgres charges.

## Hard boundaries

- No production data is involved in this rehearsal (staging only).
- The rehearsal does NOT modify the staging cluster (only reads from
  it for the snapshot + the count comparison).
- Each gate requires explicit operator approval BEFORE the command
  runs.
- If any verification fails, STOP and report — do not auto-continue.
