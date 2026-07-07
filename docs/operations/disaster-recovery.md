# Disaster recovery — production Postgres

Closes audit finding **R3** (no automated, verified, off-provider backup with a
rehearsed restore). This runbook defines the recovery objectives, the automated
backup layers, and the restore procedure for the **production** database.

It complements — not replaces — the manual, staging-scoped rehearsal in
[`staging-backup-restore.md`](./staging-backup-restore.md), which remains the
authoritative reference for the low-level `fly mpg proxy` / `pg_dump` /
`pg_restore` mechanics and the secret-handling discipline. Read that file for
command detail; read this one for cadence, RPO/RTO, offsite storage, and the
production restore path.

> Per [ADR 0003](../adr/0003-staging-deployment-strategy.md), the CLI examples
> in the ops runbooks are **dated examples, not canonical truth** — re-run
> `<command> --help` before any stateful gate and amend the runbook on a
> mismatch.

## Recovery objectives

| Objective | Target | Basis |
|---|---|---|
| **RPO** (max acceptable data loss) | **≤ 24 h** | daily automated logical dump + Fly MPG's own snapshot schedule. Tighten by increasing the `db-backup` cron frequency. |
| **RTO** (max time to restore service) | **≤ 2 h** | provision a fresh Fly MPG cluster + `pg_restore` a portable dump (Path A, the exercised path) + re-point the backend. |

These are starting targets for a small single-tenant builder; revisit if the
data volume or the business's tolerance for loss changes.

## Backup layers (defence in depth)

1. **Fly MPG provider snapshots** — in-region, automatic, frequency/retention
   per the cluster's plan tier. First line of recovery, but **same-provider**:
   it does not survive a Fly account/region loss.
2. **Automated off-provider logical dump** — the `db-backup` GitHub Actions
   workflow runs [`backend/scripts/backup_db.sh`](../../backend/scripts/backup_db.sh)
   daily. Each run produces a compressed `pg_dump` (custom format), **verifies
   it is readable** (`pg_restore --list`), records a SHA-256 checksum, and
   uploads the dump + checksum to an **off-provider** object-storage bucket.
   This is the copy that survives a Fly-side disaster.

The script is read-only against the database (`pg_dump` never writes) and is
safe to run against production. It parses the connection string, so the
password is passed via `PGPASSWORD` and never appears on a command line or in a
log. It can also be run by hand from any host that can reach the DB:

```bash
DATABASE_URL='postgresql://user:pass@host:5432/db' \
BACKUP_DIR=/var/backups/sitetracker \
BACKUP_S3_URI=s3://my-bucket/sitetracker \
  backend/scripts/backup_db.sh
```

## Enabling the automated backup (one-time operator setup)

The `db-backup` workflow is **inert until configured** — it skips cleanly with a
notice until `BACKUP_DATABASE_URL` is present, so it does not spam failures
before setup. To enable it, add these **repository secrets**:

| Secret | Purpose |
|---|---|
| `BACKUP_DATABASE_URL` | Production connection string. Prefer a **read-only** role. For Fly MPG (no direct public path), either expose a direct connection string to the runner or run the script from a host/machine that can reach the cluster (see note below). |
| `BACKUP_S3_URI` | Off-provider destination, e.g. `s3://sitetracker-backups/prod`. Use a **different provider than Fly** (Backblaze B2, Cloudflare R2, AWS S3). |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Credentials scoped to that bucket (write-only + no delete, ideally). |
| `AWS_DEFAULT_REGION` | Bucket region. |
| `AWS_ENDPOINT_URL` | Only for S3-compatible providers (B2 / R2 / MinIO). |

Then:

- **Retention / lifecycle**: configure the bucket's lifecycle policy to expire
  objects after your chosen window (e.g. 30–90 days). The CI job sets
  `BACKUP_RETENTION_DAYS=0` because the runner is ephemeral — retention is the
  bucket's job, not the runner's. (For a self-run host, set
  `BACKUP_RETENTION_DAYS` to prune old local dumps.)
- **Object-lock / versioning**: enable if the provider supports it, so a
  compromised CI credential cannot delete history (ransomware resilience).

> **Fly MPG connectivity note.** Fly Managed Postgres has no public network path
> by default; access is via `fly mpg proxy` / `fly mpg connect`. If a direct
> connection string isn't reachable from the GitHub runner, run
> `backend/scripts/backup_db.sh` from a small scheduled Fly machine (or the
> operator's backup host) that first opens `fly mpg proxy` and points
> `DATABASE_URL` at `127.0.0.1:<proxy-port>`. The script itself is transport-
> agnostic — it only needs a reachable `DATABASE_URL`.

## Verification & monitoring

- **Per-dump**: every backup run verifies the artefact with `pg_restore --list`
  and fails (non-zero exit) if the dump is unreadable — so a corrupt backup is
  never silently accepted.
- **Failure alerting**: a failed `db-backup` run surfaces via GitHub's normal
  failed-workflow notifications. **Action required:** ensure at least one
  operator receives Actions failure notifications for this repo (Watch →
  Custom → Actions), because a silently-disabled backup is the classic way this
  finding re-opens. Check the Actions tab weekly for a green daily run.
- **Restore rehearsal (the part that actually matters)**: a backup is only real
  once a restore has been proven. Run the restore rehearsal in
  [`staging-backup-restore.md`](./staging-backup-restore.md) on a **monthly**
  cadence, and — critically — capture **dump-time row counts** so verification
  can assert *exact* equality rather than the current QUALIFIED PASS (finding 6
  in that doc).

## Production restore procedure

Use this when production data must be recovered. Path A (portable dump) is the
**exercised** path; Path B (native `fly mpg restore`) remains unproven — do not
reach for it in an emergency until it is separately tested.

1. **Stop writes.** Scale the backend to zero or put it in maintenance so no new
   data lands mid-restore: `fly scale count 0 --app <BACKEND_APP_NAME>`.
2. **Pick the source dump.** Fetch the most recent verified dump + its `.sha256`
   from the offsite bucket and confirm the checksum matches:
   `sha256sum -c <dump>.sha256`.
3. **Provision a fresh cluster** (do **not** restore in place over a corrupt
   one — restore to a new cluster, verify, then cut over):
   `fly mpg create --name <NEW_CLUSTER> --region <REGION> --plan <PLAN>`.
4. **Restore** the dump into the fresh cluster using the **Path A** procedure
   and secret discipline from `staging-backup-restore.md` §"Gate R-4 → Path A"
   (`fly mpg proxy` + Docker `postgres:16` `pg_restore --no-owner --no-acl`).
   Classify any non-zero `pg_restore` exit per that doc — system-object-only
   errors are a QUALIFIED PASS; any `public.*` / failed-`COPY` error is a STOP.
5. **Verify** with the Gate R-5 queries (`alembic_version` present; `jobs` /
   `expenses` / `users` / `suppliers` counts plausible, or exact if dump-time
   counts were captured).
6. **Cut over.** Attach the backend to the restored cluster
   (`fly mpg attach <NEW_CLUSTER> --app <BACKEND_APP_NAME>`), confirm the
   `settings_loaded` / `schema_head_check` startup lines look right, then scale
   the backend back up: `fly scale count 1 --app <BACKEND_APP_NAME>`.
7. **Smoke-test** login + a read of jobs/expenses before declaring recovery
   complete. Record timings against the RTO target for the next review.

## Hard boundaries

- Never restore a dump or run `fly mpg restore` **into the live production
  cluster** — restore to a fresh cluster and cut over.
- The connection string / password never enter chat, docs, commit messages, or
  evidence files. Use repo secrets (CI) or `Read-Host -AsSecureString` (manual),
  per the discipline in `staging-backup-restore.md`.
- The dump contains real financial data — it goes only to the access-controlled
  offsite bucket, never a public artefact store.

## Open items (not yet closed by this runbook)

- **Path B (native `fly mpg restore`) is still unproven** — exercise it against
  a throwaway target and document the source/target semantics before relying on
  it.
- **Exact-count restore verification** — capture dump-time row counts so a
  rehearsal can assert exact equality instead of a qualified pass.
- **Attachment storage (Phase 5)** — when receipt/photo storage lands (S3), add
  its backup + DR story here.
