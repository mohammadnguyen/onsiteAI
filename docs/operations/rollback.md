# Rollback procedure

Procedural reference for rolling back a bad deploy or a bad migration
on the staging environment.

Per [ADR 0003](../adr/0003-staging-deployment-strategy.md): rollback
is manual for V1. App revert via `flyctl releases rollback`; DB revert
via snapshot restore from the procedure documented in
`staging-backup-restore.md`.

This document is NOT executed by Slice 2-B1, Slice 2-B2, or Slice 2-B3
(the doc + deploy + rehearsal slices). It is consulted reactively
when something is broken.

> **Fly Managed Postgres (MPG).** All database commands below use
> `fly mpg …`; the legacy `flyctl postgres …` surface does NOT apply to
> this project's MPG cluster (see the legacy→MPG mapping table at the
> bottom of `staging-backup-restore.md`). Per ADR 0003, these are dated
> examples — re-run `<command> --help` before any stateful step, as
> `fly mpg` flags drift. App-level commands (`flyctl releases …`,
> `flyctl status`, `flyctl ssh console`) are unchanged.

## Operator decision gates

Every rollback has FOUR mandatory operator decision gates:

- **Gate Roll-1.** Confirm the release-version target for app
  rollback (specific version number from `flyctl releases list`).
- **Gate Roll-2.** Confirm the DB rollback path: `flyctl releases
  rollback` only, OR `alembic downgrade`, OR snapshot restore.
- **Gate Roll-3.** Confirm overwrite of the live staging DB if
  scenario C is chosen. This is destructive of staging data.
- **Gate Roll-4.** Post-rollback verification before re-opening to
  users.

No gate may be skipped. No gate may be combined with another into a
single operator action.

## Scenario A: Bad deploy (app crashes or returns 500s)

**Detection.**

- `flyctl status --app sitetracker-backend-staging` shows machine in
  `unhealthy`.
- OR live smoke (e.g. `/healthz`) returns non-200.
- OR users / testers report errors.

**Procedure.**

1. **Gate Roll-1.** Identify the last known-good release.

   ```
   flyctl releases list --app sitetracker-backend-staging
   ```

   Note the version number of the previous deploy. Operator confirms
   the target version before proceeding.

2. Execute rollback (atomic at the Fly platform level):

   ```
   flyctl releases rollback <version> --app sitetracker-backend-staging
   ```

3. **Gate Roll-4.** Re-verify health.

   ```
   curl -sI https://sitetracker-backend-staging.fly.dev/healthz
   ```

   Expect: 200.

4. Capture incident notes: what went wrong, what got rolled back,
   what should be different about the next deploy attempt.

**Rollback class.** `Requires provider action`. Time-to-rollback:
typically < 60 seconds.

## Scenario B: Bad migration

**Detection.**

- `alembic upgrade head` broke the schema or data.
- OR the app starts but DB queries fail with schema errors.
- OR data integrity check fails post-deploy.

**Procedure.**

1. **Gate Roll-2.** Decide between alembic downgrade (if the
   migration has a working `downgrade()`) vs scenario C (snapshot
   restore).

   Check the offending migration file in `backend/alembic/versions/`.
   If `downgrade()` is implemented and not marked broken, attempt
   downgrade:

   ```
   flyctl ssh console --app sitetracker-backend-staging
   cd /app && uv run alembic downgrade -1
   exit
   ```

   If `downgrade()` is missing, broken, or known data-lossy → fall
   through to scenario C.

2. **Gate Roll-4.** Verify schema returned to the previous head.

   ```
   fly mpg connect <CLUSTER_ID>
   SELECT version_num FROM alembic_version;
   ```

   Expect: the previous head SHA.

**Rollback class.** `Reversible via manual operator action` (if
alembic downgrade works) OR `Requires provider action` (if scenario C
is needed).

## Scenario C: DB restore from snapshot

**Detection.**

- Scenario A or B insufficient.
- OR data corruption detected that pre-dates the bad deploy.
- OR specific data needs to be rolled back to a specific point in
  time.

**Procedure.**

1. **Gate Roll-1.** Identify the target snapshot.

   ```
   fly mpg backup list <CLUSTER_ID> --json
   ```

   Note the snapshot ID + timestamp. Operator confirms the target.

2. **Gate Roll-3.** Confirm overwrite of staging DB. This is
   destructive: data written to staging AFTER the snapshot timestamp
   will be LOST.

   Operator explicitly confirms before proceeding.

3. Execute restore. **SYNTAX UNVERIFIED.** Native Fly MPG restore
   (Path B) has NOT been exercised — its source/target semantics are
   ambiguous (which backup restores into which cluster), so getting it
   wrong risks touching the wrong cluster. Re-run `fly mpg restore
   --help` and confirm the semantics BEFORE running this against the
   live staging cluster. Prefer Scenario B (alembic downgrade) when the
   offending migration has a clean `downgrade()`.

   ```
   fly mpg restore <CLUSTER_ID> --backup-id <BACKUP_ID>
   ```

4. **Gate Roll-4.** Verify.

   ```
   fly mpg connect <CLUSTER_ID>
   SELECT version_num FROM alembic_version;
   SELECT COUNT(*) FROM jobs;
   SELECT COUNT(*) FROM expenses;
   \q
   ```

   Counts should match the snapshot's content (NOT the live state as
   of right before the restore).

**Rollback class.** `Potentially irreversible` (data written between
snapshot and restore is lost).

## Production rollback

This document covers staging only. Production rollback procedures
will be a separate document when a production environment exists
(separate later slice).

## Hard boundaries

- No rollback action runs without an operator at the console.
- No automated rollback triggers (e.g. on health-check failure) —
  manual only for V1.
- No production environment to roll back to/from in V1.
- All four operator decision gates required for any rollback
  scenario.
