# Production release plan — Site Log (`bcc3964`)

**Status: PREPARATION. No release step has been executed** — no backup,
migration, deploy, app build or secret change. What has run, each under its
own approval: the read-only checks P-2 to P-6, and R1 (section 13), which
includes a production storage test that wrote labelled test objects to the
bucket and then deleted them (no database write). This is the plan the
founder approves or rejects, step by step. Every stateful step below needs its
own explicit approval; read-only checks need approval too, because they run on
the production machine.

"Production" here is the Fly app **`sitetracker-backend-staging`**. Its name
is historical: it carries the operator's real business data and serves the
daily Forey app (`com.forey.app`). The runbooks it was built with are titled
"staging"; they still govern it (see section 6).

Upload caps are **not** part of this release and must not change:

| | Client (`mobile/app.config.ts`) | Server |
|---|---|---|
| Forey (real) | 26,214,400 bytes (25 MiB), 180 s | default 26,214,400 — no `EVIDENCE_MAX_UPLOAD_BYTES` secret |
| Forey Test | 52,428,800 bytes (50 MiB), 300 s | `EVIDENCE_MAX_UPLOAD_BYTES=52428800` on `forey-test-api` only |

A result obtained on Forey Test does not carry over where these differ. The
28,163,249-byte drawing that uploads on Forey Test is **refused at pick time**
by the real app, with a message naming the 25 MiB limit.

## 0. Where this stands (after R1, 2026-10-01)

**Established read-only (P-2 to P-6):** production runs `6036491` on database
revision `c7d8e9f0a1b2` (PostgreSQL 16.14); `APP_ENV=staging`, configuration
from environment variables only (no env file is loaded); evidence storage is
`s3` on the Tigris endpoint, with the configured bucket and endpoint equal to
the Tigris-attached ones; the effective upload cap is 26,214,400 (code
default); the bucket has no lifecycle rules and no incomplete multipart uploads
under `evidence/`; the Site Log tables and the `evidence` and
`evidence_audit_log` tables hold no rows.

**Established by R1 (section 13):**
- **R1-a, synthetic rehearsal — passed.** On a local scratch database, on the
  **migrated** schema, the old code (`6036491`) gave the expected status for
  all 46 scripted calls, reads and writes: 43 on Build 4's endpoints, covering
  39 of its 42, plus three calls Build 4 does not make. One of the 46 was first
  sent wrongly by the test driver and re-run as Build 4 sends it. Not exercised
  on the migrated schema: review-queue resolve and reject, and user invite. The
  new code (`bcc3964`) passed 23 of 23 calls: 15 on Site Log and evidence paths
  (job-assigned captures only) and 8 daily-flow reads. The old code still read
  a database the new code had written to (11 of 11 sampled reads). The
  migration took about a second. Migrate-first is now executed evidence, not
  only code inspection. Limits: section 13.
- **R1-b, production storage operations — passed, cleaned.** With the
  production credentials, in `evidence/`: multipart create, part upload,
  complete, HEAD (absent before copy), copy, staging delete, GET read-back with
  matching SHA-256, HEAD on the final object, and abort. Every test object was
  deleted by exact key and verified absent; a read-only follow-up found bucket
  versioning never enabled, snapshots off, and no object version, delete
  marker, object or incomplete upload under the test prefixes. This proves
  those **operations and permissions** — not the new adapter's code as a whole,
  and not clean-up after a hard process death.
- **Env files in the image:** in both env files, on every line including
  comments, no current production credential component was found; the files
  do hold a loopback (local development) database URL with a password. The
  `.env*.example` templates were not checked. Not proof of safety (section 10).
- **Tigris lifecycle:** its documentation lists Expiration and Transitions with
  prefix filters; `AbortIncompleteMultipartUpload` is not documented (7.2).

**What still blocks release:**

| For | Blocker | Why it is open |
|---|---|---|
| Backend migrate + deploy | Daily app facts | The installed build (Diagnostics) and Build 4's availability as a fallback — the founder is checking |
| Backend migrate + deploy | The release package itself | Backup, one-off migration machine, deploy — not yet approved |
| New app | S-1 on the deployed `bcc3964` | The new adapter's own code paths with labelled test objects (R1-b used raw operations from the old machine) |
| New app | Gate 2 procedure | No documented lifecycle rule for incomplete uploads; the manual check and exact-reclaim procedure (7.2) and its schedule need approval; residue the reclaim cannot remove (completed staging objects, legacy uploads left `pending`) is accepted only by decision |
| Backend deploy (D-2) and new app (N-1) | Production write-smoke policy | The labelled test expense (create, then delete) or real entries only — a founder decision (section 12) |

Not a release blocker: the env files leave the image with the clean build
(P-9); a `.dockerignore` is a later code slice.

---

## 1. Measured starting point (2026-10-01)

Measured read-only; nothing was changed. Method: Fly status, releases, image
and config queries; secret **names** only; the deployed source read inside the
container and matched to git by blob hash; the Alembic revision read with
Alembic's own `MigrationContext` inside a PostgreSQL `READ ONLY` transaction
(no `env.py`, no app import, no bytecode written, the database URL never
printed).

| | Measured | Target |
|---|---|---|
| Deployed backend code | **`6036491`** — all 104 deployed files under `app/`, `alembic/` and top-level config match that commit exactly | **`bcc3964`** |
| Release | v29, 2026-09-03 00:22 UTC, one machine, `syd`, 512 MB | — |
| Database revision | **`c7d8e9f0a1b2`** (one row) — the Site Log tables already exist | **`d9e0f1a2b3c4`** |
| Daily app | latest production-profile build = **Build 4, from `6036491`** (2026-09-03). Installed build on the phone: UNVERIFIED | new production build of `bcc3964`, last |
| PostgreSQL | **16.14** | — |
| `APP_ENV` / config source | **`staging`**, environment variables only — no env file loaded (P-3) | unchanged |
| Evidence storage | **`s3`** on the Tigris endpoint; configured bucket and endpoint equal the Tigris-attached ones; endpoint carries no credentials; client signs as `us-east-1` and Tigris accepts it (P-3, P-4) | unchanged |
| Upload cap | **26,214,400** effective — the code default; not in the environment, not from a file (P-3) | unchanged |
| Token lifetimes | access 60 min, refresh 30 days (P-3) | unchanged |
| Rows | Site Log tables 0; `evidence` 0; `evidence_audit_log` 0. Core-table counts recorded in the private ops journal (local, outside this repository) as the backup baseline (P-6) | — |
| Bucket | no lifecycle rules; 0 incomplete multipart uploads under `evidence/` (P-5) | — |

The image carries no git label, and the registry answers `NAME_UNKNOWN` for
the operator token, so the blob-hash comparison inside the container is the
only way the deployed commit was pinned.

## 2. The gap

Seven commits, all on `main`: `e302933` (#11 A1b), `723bb6e` (#13 A2a),
`78b5e2b` (#14 WP-S-core), `b11bf57` (#16 docs), `47e03d0` (#17), `812b8ae`
(#18), `bcc3964` (#19).

**One migration, `d9e0f1a2b3c4`.** It adds
`site_log_event_attachments.upload_attempt_no INTEGER NOT NULL DEFAULT 0` and
the CHECK `ck_slog_attachment_attempt_nonneg (upload_attempt_no >= 0)`. No
backfill, no index, no other table. Both operations run in one transaction
(`env.py`), so they commit together or not at all. Its downgrade drops the
constraint and the column; its own docstring says to prefer a forward
migration once uploads have used the counter.

**Backend files that change** (outside tests): the migration; `api/evidence.py`;
`api/router.py` (adds the Site Log router — new paths only, no collisions);
`api/site_log.py`, `schemas/site_log.py`, `services/site_log/*`,
`services/site_log_access.py` (new); `models/site_log.py` (the new column);
`services/evidence.py`; `services/evidence_storage.py`.

**Unchanged:** `config.py`, `main.py`, `database.py`, `deps.py`, `Dockerfile`,
`fly.toml`, `pyproject.toml`, `uv.lock`, `alembic.ini`, `alembic/env.py`, and
every auth, expense, labour, jobs, reports, users, categories, suppliers,
org-settings and review-queue module. No new setting, default or validator.

### Compatibility — derived from the code, not assumed

| Combination | Result | Basis |
|---|---|---|
| **Old code (`6036491`) on the new schema** | Works | `6036491` has no API or service that reads or writes any Site Log table; its ORM does not map the new column, and ORM selects name their columns. An insert would take the server default 0. **Rehearsed 2026-10-01 (R1-a, section 13): 46 of 46 scripted calls passed on the migrated schema — 43 on Build 4's endpoints, covering 39 of its 42 (not review-queue resolve and reject, not user invite); one call re-run after a test-driver error — and 11 of 11 sampled reads after the new code wrote.** |
| **New code (`bcc3964`) on the old schema** | **Breaks** | Every select of `SiteLogEventAttachment` names `upload_attempt_no`. That includes all Site Log routes **and** the existing `GET /evidence/{id}`, `/download` and `POST /evidence/{id}/link-job`, which now call `binding_for_evidence`. Startup does not catch it: the schema check only logs. |
| **Build 4 (old app) on the new backend** | Works (contract) | Build 4 makes 42 calls (auth, expenses, labour, jobs, reports, users, categories, suppliers, org settings, review queue). The modules behind them are byte-identical except `router.py` (additive) and `models/site_log.py` (a table Build 4 never reaches). Build 4 makes no `/evidence` or `/site-log-events` call. |
| **New app on the old backend** | **Breaks** | No Site Log router at `6036491`: the list fails and every save stops at its first lookup (`siteLog.error.lookup`) before anything is written. |

Therefore: **migrate first, then deploy; backend before the app.**

### Behaviour changes to existing endpoints

- **Stricter S3 write precondition.** At `6036491` the S3 `put` ignored any
  error from the HEAD on the final key and copied anyway. At `bcc3964` it
  copies only after a HEAD answered 404; a 403 or 5xx becomes a storage error
  (502). If the production credentials answer 403 for a missing key, **every
  upload fails** — `POST /evidence` included. This is Gate 1 (section 7).
- Evidence attached to a Site Log event is read under the event's access rule;
  `link-job` refuses such evidence with 409.
- `POST /evidence` records failed uploads as failed; on S3 an oversize upload
  becomes 413 instead of 502.

Build 4 calls none of these, so the daily flows are not affected by them.

## 3. Release order

Each step: what it does, what proves it worked, and when to stop. Steps marked
**(approval)** change or touch production and need their own go.

### Pre-flight — read-only, nothing changes

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **P-0** | Freeze scope: from `6036491`/`c7d8e9f0a1b2` to `bcc3964`/`d9e0f1a2b3c4`. No secret is set or unset in this release. | Founder confirms both SHAs | Anyone proposes a secret write, a cap change or a different target |
| **P-1** | Record the rollback target: current release (v29), its image reference and digest, the machine, and the kill signal/timeout. Confirm the rollback command against `flyctl releases rollback --help`; if unsupported for Machines, rollback is `flyctl deploy --image <v29 ref>`. | Written in the private ops journal | Live release is not v29 or the image differs from the measured one (drift since 2026-10-01) |
| **P-2** | Database revision, read as measured: `MigrationContext` in a `READ ONLY` transaction, from the image's `/app/.venv`, `python -B`. **Never `uv run`** (it re-syncs packages inside the production container). **Done 2026-10-01: `c7d8e9f0a1b2`.** Re-run immediately before the window. | Exactly one revision, `c7d8e9f0a1b2` | Anything else. Do not install, sync or repair — report |
| **P-3** | Effective configuration through the deployed settings loader (cwd `/app`), printing no secret: `APP_ENV`, the env file the loader resolves, storage backend, endpoint host, bucket, the effective cap and its source, token lifetimes, booleans. **Do not rely on the `settings_loaded` startup log line** — the app's INFO logs are very likely not emitted (no logging configuration; uvicorn leaves the root logger at WARNING). **Done 2026-10-01: `staging`, no env file, `s3`, cap 26,214,400 from the default** (section 1). Re-run on the new machine after D-1. | `APP_ENV` is `staging` or `production`; no env file loaded; `s3`; cap 26,214,400 | An env file is loaded, or `APP_ENV=development` |
| **P-4** | Storage Gate 1 probe — section 7.1. **Done 2026-10-01: bucket 200, evidence-shaped key 404.** | `head_bucket` 200 **and** `head_object` 404 | Any 403/301/400, a 404 on the bucket, or 5xx/timeout (inconclusive, not a pass) |
| **P-5** | Storage Gate 2 reads — section 7.2. **Done 2026-10-01: no lifecycle rules; 0 incomplete uploads under `evidence/`.** | Counts and lifecycle rules recorded | An Expiration rule covers `evidence/` (a retention hazard, DEC-EVIDENCE-001) |
| **P-6** | Aggregate counts only, in a `READ ONLY` transaction, plus `server_version_num`. **Done 2026-10-01: PG 16.14; Site Log tables 0; `evidence` 0; core counts in the private ops journal.** Re-run immediately before the window as the backup baseline. | Server ≥ PG 11; attachment count recorded | PG < 11, or unexpected Site Log rows — re-plan the lock window |
| **P-7** | Client inventory: the phone's Settings → Diagnostics shows commit `6036491`. | `6036491` | Any other build — redo the old-app compatibility check for that build first |
| **P-8** | Synthetic rehearsal off production — see "Rehearsal" below and section 13 (R1-a). **Done 2026-10-01: passed** (46/46 old-code calls on the new schema, one re-run after a test-driver error; 23/23 new-code calls; 11/11 old-code reads after the new code wrote; coverage limits in section 13). | Every scripted call passes on both versions | Old code fails on the new schema, or the new code's normal path fails |
| **P-9** | Build the target image **once**, from a **clean worktree at `bcc3964`** (tracked files only; `backend/` has no `.dockerignore`, so building from a working copy copies untracked local files such as `.env.*` into the image). Build-only and push (e.g. `flyctl deploy --build-only --push`; dated example — confirm with `--help`). Record the image digest, base-image digest and uv version (`python:3.12-slim` and `pip install uv` are not pinned). | Digest recorded; build context contains no `.env*` except `*.example`, no `.venv`, no `var/` | Build fails or the context holds untracked env files |

**Rehearsal (P-8) — synthetic data, not production data.** Two claims rested
on code inspection alone: that old code runs on the new schema, and that the
new code's normal path works on it. Both were rehearsed on 2026-10-01 on a
local scratch database filled with synthetic data and passed (section 13,
R1-a). Not covered: review-queue resolve and reject and user invite on the
migrated schema; the new code's unassigned-capture, job-assignment and
attachment-reset paths; the S3 path; real-data volume. If the release target
changes, re-run it. Do **not** use the `6036491`
test suite for the first: its fixture drops the schema and rebuilds it from
`6036491`'s own models, so it would test the old schema and pass falsely —
run the `6036491` app against an Alembic-upgraded database and script Build 4's
calls instead.

Copying production data is **not** part of the default plan. The case for it
was the migration's lock time on real volume, and P-6 removed it: the altered
table has 0 rows and the server is PostgreSQL 16, so adding the column with a
constant default is catalog-only and the CHECK validates an empty table. If the
synthetic rehearsal leaves a specific gap that only real data can close, it is
named and requested separately.

### Backup — immediately before the first write

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **B-1 (approval)** | Native backup — Gate R-1 unchanged. | Backup ID with status completed; its stop time is the restore point | Pending or failed — do not migrate |
| **B-2 (approval)** | Portable dump — Gate R-2 unchanged (three-guard `pg_dump`), plus dump-time row counts. Path A (portable dump) is the only restore path ever exercised (2026-06-02). | `pg_dump` exit 0, SHA-256 recorded, `PGPASSWORD` cleared, dump outside the repo and OneDrive | Any guard fires |

### Migrate first, while v29 keeps serving

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **M-1 (approval)** | `cd /app && python -B -m alembic upgrade d9e0f1a2b3c4` — explicit revision, never `head`, never `uv run` — **from the new image** (the v29 image has no `d9e0f1a2b3c4` script), on a **one-off machine with no service** in the app, removed when it exits (founder direction, 2026-10-01). It gets the app's secrets and so the database; it must not attach to the HTTP service. Command shape: `flyctl machine run <P-9 image> --app sitetracker-backend-staging --rm …` (dated example — confirm flags, and that no service or port is configured, with `--help`). | `alembic current` (new image) shows `d9e0f1a2b3c4`; a read-only count of `upload_attempt_no > 0` is 0; attachment count equals P-6; no 5xx from v29 | The migration errors (it rolls back as one transaction — verify the revision is still `c7d8e9f0a1b2`), or a lock wait longer than a few seconds |
| **M-1 abort** | There is no `lock_timeout`. To abort a lock wait: from a separate approved read-only-plus-cancel session, find the migration's backend in `pg_stat_activity` and `pg_cancel_backend` it (terminate if cancel fails). **Before retrying or deploying, confirm no ALTER on `site_log_event_attachments` is still waiting or active.** Killing the client alone may leave the ALTER queued server-side. | No migration backend remains | — |
| **M-2** | Soak old code on the new schema: on Build 4, cold open, Home, Jobs detail and budget summary, Labour, review queue — reads only unless a write smoke was approved. | All pass; no 5xx | Any daily flow fails — see rollback |

### Deploy

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **D-1 (approval)** | Deploy the exact P-9 image: `flyctl deploy --image <P-9 ref> --app sitetracker-backend-staging` (confirm flags). Outside site hours, founder warned — the single machine is replaced, so expect a short gap. Build 4 keeps its session through transport errors and 5xx. | Machine running the P-9 image; **P-3 re-run on the new machine** gives the same answers; `alembic current` from it = `d9e0f1a2b3c4`; **a read-only ORM select of one `SiteLogEventAttachment` through the new models** in a `READ ONLY` transaction succeeds (the only check that exercises the new column — `/healthz` never touches the database, `/auth/me` reads only users, and `/site-log-events/mine` touches attachments only when events exist); `GET /auth/me` 200 with the founder's session; no 5xx | Any of those fails — rollback D-1 |
| **D-2** | Build 4 against `bcc3964` — section 8, list C. | All ten items pass; no 5xx during the smoke | Any daily-flow failure — rollback D-2 |

### Storage write evidence — before any new app

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **S-1 (approval)** | Upload chain with the production credentials, through **the new adapter's own code** on the deployed `bcc3964` machine, writing **storage only** (no database row): a small number of clearly labelled test objects at freshly generated, recorded `evidence/<uuid4>/…` keys — a multi-part object and a single-part one through `put`, read back through `open` and compared by SHA-256, `exists` on the final key, and one deliberately failed `put` that must abort its multipart upload. **Not** the first real business capture. R1-b (section 13) ran the same API operations raw before the window and passed; S-1 repeats them through the code that will serve. | Every operation succeeds; read-back matches; abort leaves no incomplete upload; only the recorded keys exist | Any 403/5xx, a read-back mismatch, or residue. Keep the new app unbuilt; the backend can stay (Build 4 never touches storage) |

### New app — last

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **N-1 (approval)** | Production-profile build of `bcc3964` (`FOREY_VARIANT=default`, `com.forey.app`, 25 MiB / 180 s). The operator submits. It **replaces Build 4** on the phone (same bundle id, no OTA). Install on a second device first if one exists. | Section 8 list B | Any failure — rollback N-1 |
| **C-1** | Close-out (Gate D-10, rewritten for production): release and image digest, deployed SHA = `bcc3964`, `alembic current`, backup ID and dump SHA, P-3 to P-6 results, M-2/D-2/N-1 results, multipart and residue counts after release. | Ops journal complete; nothing sensitive in the repository | — |

## 4. Rollback

The downgrade of `d9e0f1a2b3c4` is **never needed for a code rollback**: old
code tolerates the column. It is lossless only while no upload has used the
counter, so any downgrade, at any stage, is preceded by a read-only
`count(*) WHERE upload_attempt_no > 0` = 0 — the check decides, not the stage.
A downgrade must run from the **new** image; the v29 image has no
`d9e0f1a2b3c4` script. **Never run Alembic from the v29 image during a
rollback.**

| Failure at | Action | Data loss | Downgrade |
|---|---|---|---|
| P-0 to P-9 | Stop. Production unchanged. Destroy any disposable rehearsal cluster (R-6). | None | No |
| B-1 / B-2 | Stop before M-1; retry in a new window. Never migrate without both backups. | None | No |
| M-1 | The single transaction rolls back by itself. After any abort, confirm no migration backend remains, then `alembic current` = `c7d8e9f0a1b2`. v29 is unaffected. | None | Nothing to downgrade |
| M-2 (not expected from the code) | Do not deploy. Prove the failure is schema-caused. If it is: downgrade from the new image after the count check (0 here by construction — no new code has run). | None — only the all-zero column and its CHECK are dropped | Only here, only if proven |
| D-1 | Code-only rollback to v29 (P-1 command). Schema stays at `d9e0f1a2b3c4`. Recheck `/auth/me` 200 and list C items 1–3. | None persisted. Requests in flight during the swap may fail. Anything the new code wrote to unchanged tables stays readable by v29. | No |
| D-2 | As D-1. Build 4 needs no action. | None | No |
| S-1 | Keep `bcc3964` serving; do not build the app. Tigris key policy or bucket settings are a founder decision; **no secret is rotated** in this release. Rolling back code does not help (v29 has no Site Log and its `POST /evidence` hid HEAD errors). Record residue by count. | None in the database; a probe or staging object may remain until an approved cleanup | No |
| N-1 | Stop distribution. The backend stays on `bcc3964` (compatible with Build 4). Returning the phone to Build 4 via TestFlight is allowed **only if Build 4 is unexpired and the phone holds no unsent Site Log drafts and no kept recordings** — Build 4 has no index for either, so they would be stranded and unreachable; whether they survive a downgrade and re-upgrade is UNVERIFIED. | Server data unaffected | No |
| **After the new app has shipped** (any later backend rollback) | A code-only rollback to v29 removes every Site Log route, so the new app's Site Log breaks (404 at its first lookup). Pair the rollback with a phone decision: downgrade phones to Build 4 under the N-1 guard, or announce a Site Log outage. Uploads in flight during the swap are killed: their rows stay `pending`, v29 has no reset, and after `bcc3964` returns an admin reset is needed (15-minute minimum age). Forward-fix schema problems with a new migration. | No committed data lost; pending uploads need an admin reset; any multipart or staging residue is counted, never bulk-deleted | **Never** once the count check is non-zero |
| Data corruption a code rollback cannot fix | Scenario C of `rollback.md`, with all four Roll gates. **Restoring into the live cluster has never been rehearsed** and `staging-backup-restore.md` forbids it, so it needs its own approved plan: rehearse on a disposable restore first, then choose between a Path A restore into a fresh cluster plus re-attach (a resource and secret change — separate full-gate decision) and a native restore once its semantics are confirmed. | Section 5 | Restore replaces it; after a restore to the pre-migration backup the database is at `c7d8e9f0a1b2`, so `bcc3964` must not serve until migrated again |

## 5. Backup and restore — what a restore loses

A restore rewinds the **whole database** to the backup's timestamp and loses
every write committed after it: expenses and parsed captures; labour entries
and workers; job, budget and alias edits; review-queue resolutions; user
invites and role changes; org settings and suppliers; and, once `bcc3964` has
served, every Site Log event, revision, attachment, audit and eligibility row,
every evidence row and every attempt counter.

A database restore **does not touch Tigris**. Objects written after the
backup stay in the bucket as orphans with no rows. They are counted and
handled under a separate retention decision (DEC-EVIDENCE-001), never
bulk-deleted. With the backups this plan takes (before M-1, when no attachment
rows exist), orphaned objects are the only storage effect: new rows get new
evidence ids, so no existing key can be reused.

A restore is the right tool only for corruption or loss that a code rollback
and a forward fix cannot repair. It is **not** the tool for a failed migration
(it rolls itself back), a bad deploy (roll the code back), or an old-code
incompatibility (the code shows none).

## 6. Upgrading the live environment is not creating one

Reused, adapted: **R-1, R-2** (backups, as written, plus counts); **D-7**
(migration) moved **before** D-6, run from the new image's own virtualenv with
an explicit revision; **D-6** (deploy) as build-only from a clean worktree,
then deploy by image; **D-9** smokes using the founder's own session (no
account is created); **D-10** rewritten; `rollback.md` Scenario A, Scenario B
only under the count check, Scenario C only with its own plan; **R-3 to R-7**
only against a disposable cluster.

**Forbidden in this upgrade** — each creates, resets or rotates something the
live environment already has:

- D-1 `fly apps create`; D-2 region set; D-3 `fly mpg create`; any
  `fly mpg destroy` against the live cluster.
- D-4 `fly mpg attach` or `detach` — re-issues or removes `DATABASE_URL`.
- D-5 and any other `flyctl secrets set/unset` — a new `JWT_SECRET` signs
  every Build 4 session out (a 401 from refresh is fatal in the app); it could
  also flip `APP_ENV`. This includes `EVIDENCE_MAX_UPLOAD_BYTES`.
- D-8 `seed_admin` — re-running it resets the named admin's password, name and
  role.
- `fly scale count`, machine destroy/recreate beyond what `flyctl deploy`
  does, any restore into the live cluster without the Scenario C plan, and any
  Tigris bucket create, reset or credential rotation.

The only transient resource this plan uses is the one-off, no-service
migration machine (M-1), removed when it exits.

## 7. Storage release gates

What the code at `bcc3964` relies on from the provider (`evidence_storage.py`):
a HEAD on a missing key returns 404 (needs list rights; without them
S3-compatible stores answer 403); error codes the classifier maps to
transient/permanent; the multipart sequence (create, 5 MiB parts, complete,
HEAD on the final key, copy, delete staging); read-after-write (copy sees the
just-completed staging object; GET sees the final object); abort or delete on
failure and cancellation with a 10 s grace. Bucket and endpoint come from
`EVIDENCE_S3_BUCKET` and `EVIDENCE_S3_ENDPOINT_URL`; credentials and region
from the standard `AWS_*` variables through the SDK's default chain.

**What an isolated test bucket with its own credentials can prove:** Tigris's
generic behaviour — HEAD 404 vs 403 under *that* key's role, error-code shapes,
multipart/abort/copy semantics, read-after-write, SDK checksum-header
acceptance, region signing.

**What it cannot prove:** the production key's role and policy scope; the
production bucket's own settings; and write rights with the production
credentials. Those needed production evidence: P-4 and R1-b supplied it for
the storage operations (below); the new adapter's own code still needs S-1.

What the evidence supports about production writes by the app, and no more:
no client of the deployed release calls the upload path; the production
database holds no `evidence` or `evidence_audit_log` rows, so the deployed app
has never recorded an upload attempt, successful or failed; the bucket had no
incomplete multipart uploads under `evidence/` (P-5). Whether any object was
ever written to the bucket by other means before R1 is not established, and
the bucket was not listed. R1-b's own labelled test objects were written and
then deleted (section 13). A HEAD that answers correctly, or a policy that
looks permissive, is **not** a verified upload chain.

**R1-b (2026-10-01)** then exercised the operations themselves with the
production credentials in `evidence/` — create, part upload, complete, HEAD,
copy, staging delete, GET read-back, abort — and passed (section 13). That
establishes the **operations and permissions**. It does not establish the new
adapter's own code paths (classification, cancellation, supervised clean-up),
which run first in S-1 on the deployed `bcc3964`, nor any clean-up after a hard
process death.

**An isolated-bucket result never closes a production gate.** Gate 1 closes
only after S-1 runs the new adapter's own code on the deployed `bcc3964`; the
probe (7.1) and R1-b are inputs to it, not its closure. Gate 2 closes only on
the founder's approval of the manual check, the exact reclaim and their
schedule (7.2), and a decision on the residue the reclaim cannot remove
(completed staging objects; incomplete uploads of legacy rows left `pending`);
the P-5 reads are inputs, not its closure. Neither gate is open or closed automatically.

### 7.1 Gate 1 — minimal read-only probe with the production credentials (run 2026-10-01)

- **Where:** inside the production machine, `cd /app`, `python -B`, using the
  app's own configured client — the credentials never leave the VM.
- **Never through `exists()`:** at `6036491` it returns False for *any*
  exception, so a 403 would read as "absent" — a false pass.
- **Calls:** `head_bucket` on the configured bucket, then `head_object` on a
  key of **the exact shape the new code HEADs before every copy**:
  `evidence/<fresh uuid4>/<16 hex>.a1` (`make_object_key` with an attempt
  number). The probe must sit in the `evidence/` namespace: Tigris supports
  prefix-scoped policies, so a key elsewhere could answer 404 while
  `evidence/` answers 403 — a false pass. A fresh uuid4 is not any existing
  evidence id (those are uuid4s the app minted; a collision is negligible, and
  a 200 is treated as "stop", below). Both are HTTP HEAD requests; nothing is
  written.
- **Prints:** the HTTP status and error code of each call, the client's region
  name, and two booleans (configured bucket equals the Tigris-attached bucket;
  configured endpoint equals the Tigris endpoint). No credentials, bucket name,
  endpoint, keys or listing. Never `str(exc)`.
- **Reading it:** 200 then 404 = pass. 404 on the bucket = bucket missing
  (a HEAD cannot tell a missing bucket from a missing key without it) — fail.
  403 on either = no list rights or bad signing — fail; with `bcc3964` every
  upload would then fail. 301/400 = misconfigured — fail. 5xx or timeout =
  inconclusive. 200 on the probe key = an object exists at a freshly random
  evidence key — stop and investigate.
- **What it does not prove:** write, copy or delete rights in `evidence/`,
  and GET rights for downloads — R1-b later established those operations
  (section 13); the new adapter's own code still needs S-1.
- **Result, 2026-10-01:** `head_bucket` 200; `head_object` on a fresh
  evidence-shaped key 404. Gate 1's **HEAD precondition** holds for the
  production credentials in `evidence/`. R1-b then verified the storage
  operations themselves (section 13). Gate 1 closes only after S-1 runs the
  new adapter's own code on the deployed `bcc3964`.
- **Side effects:** a short-lived SSH certificate; one extra Python process on
  a 512 MB machine (check `free -m` first); two billed HEAD requests.

### 7.2 Gate 2 — incomplete multipart uploads on hard death

A deploy or restart during an upload kills it with no cleanup: an incomplete
multipart upload stays in the bucket, and the row stays `pending` until an
admin reset. A death **after** `complete_multipart_upload` leaves a
*completed* object at `evidence/{id}/.staging[.aN]`. A lifecycle
`AbortIncompleteMultipartUpload` rule handles the first case only; **no
prefix-safe Expiration rule can remove the second**, because it shares the
`evidence/{id}/` prefix with real evidence and lifecycle filters cannot match
a suffix. (The module docstring leaves this residue to a bucket lifecycle
policy. In production there is none — P-5 found no rules — and Tigris documents
no abort-incomplete rule, so today nothing automatic covers either case.)

Read-only checks, each needing approval, printing counts and flags only:
`list_multipart_uploads` under `evidence/` (upload count, truncation, count
older than 24 h — no keys); `get_bucket_lifecycle_configuration` (per rule:
status, whether `AbortIncompleteMultipartUpload` is present and its days,
whether Expiration is present, the filter prefix). If the app key lacks these
rights, the operator reads the Tigris console. A listing that counts completed
staging objects is a bucket listing, so it is a separate founder decision.

**Result, 2026-10-01:** the bucket has **no lifecycle rules**; **0**
incomplete multipart uploads under `evidence/`.

**Two problems, two treatments — one rule does not solve both:**

| Residue | Treatment | Not acceptable |
|---|---|---|
| Incomplete multipart uploads (death before completion) | **No documented lifecycle rule** (see below). Manual check and exact reclaim, below — except a legacy upload whose row stays `pending`, which the reclaim cannot establish as dead: known residue, accepted only by decision. | Treating any lifecycle rule as having solved it; aborting by age alone |
| Completed staging objects `evidence/{id}/.staging[.aN]` (death after completion) | No lifecycle rule can target them safely. Count them periodically (a listing — founder decision) and accept them as known residue until a deliberate, reviewed cleanup tool exists. | **Any Expiration rule covering `evidence/`** — it would delete real evidence (DEC-EVIDENCE-001) |

**What Tigris supports (its documentation, checked 2026-10-01):** lifecycle
rules support Expiration and Transitions (to colder tiers), scoped by
`Filter.Prefix`, up to 10 rules per bucket. `AbortIncompleteMultipartUpload` is
**not documented**, and the multipart page says only that it is good practice
to "occasionally list and abort stale multipart uploads". Until Tigris
documents it, it is treated as unsupported; no rule is set. The S3 API
operations the manual route needs are documented as supported
(`ListMultipartUploads`, `AbortMultipartUpload`), and R1-b exercised the abort
with the production credentials.

**Minimal manual check (read-only):** `ListMultipartUploads` under `evidence/`
— count and age only in any report; keys and upload IDs kept in the private
ops journal. Run it before and after every backend deploy, and on a schedule
the founder sets.

**Exact reclaim, per upload, never by prefix:** for each upload older than an
agreed age, take its key and upload ID, and read the matching rows
(read-only). Two key forms exist:

- `evidence/{evidence_id}/.staging.aN` — a Site Log attachment attempt: read
  the `evidence` row and the attachment row. Reclaim only when the attachment
  has moved past attempt N (an admin reset) or the attempt is recorded as
  failed.
- `evidence/{evidence_id}/.staging` (no suffix) — the legacy `POST /evidence`
  path, which has no attachment row: read the `evidence` row only. Reclaim only
  when that row is recorded as failed. **A legacy row left `pending` by a hard
  death never becomes `failed`** — the code has no reset for it (it finds
  abandoned rows manually: `pending` older than a day) — so its incomplete
  upload is **not reclaimable under this procedure**. Age alone does not prove
  the writer is dead, so it is not used instead. Such uploads are known
  residue, like completed staging objects, accepted only by founder decision.
  No released client calls `POST /evidence` (Build 4, the new app and the admin
  web do not), so this arises only from direct API use, such as a test.

Then `AbortMultipartUpload` with that exact key and upload ID, and confirm it
no longer lists. Each reclaim is recorded in the private ops journal and needs
the founder's approval of the exact list; anything whose owner or state cannot
be established is left and reported.

## 8. Regression plan

Only four things in the mobile JS depend on the variant: the API URL, the
upload limits (read only by Site Log), the Diagnostics variant row, and the
native identity. Auth, refresh, logout, expense, labour, jobs and export are
the same source in both variants. They do run through a rebuilt native binary
and a new `babel.config.js`, so "unchanged JS" is not "unchanged app" — every
daily flow is re-run, not only the changed ones.

**A — on Forey Test (shared JS; valid because the code is identical).** The
Forey Test acceptance checklist has no expense, labour, jobs or refresh
items, so whether Build 7 exercised them is unrecorded; run them explicitly:
login; refresh after the access token expires (default 60 min — wait it out;
changing the lifetime on `forey-test-api` is a secret write); logout without
drafts (straight to login; an edge-swipe cannot return to the tabs) and with
drafts (count named; cancel keeps everything; confirm removes only that
account's drafts); failed-capture texts wiped on explicit logout, kept on an
involuntary one; expense parse → save → list → detail; labour; jobs; PDF
report and Excel export.

**B — only under production configuration.** Identity (`com.forey.app`,
"Forey", no variant row, production host, commit `bcc3964`); in-place upgrade
over Build 4 keeps the session (keychain tokens, `userId` from the old
token); the 25 MiB refusal at pick time for a 28,163,249-byte file; the
effective server cap and `APP_ENV`; Site Log upload and download through S3
(a production write — founder decision); first-run microphone and photo
permission prompts; real roles and data volume. To avoid replacing the daily
app early: run list C first (no new build needed), then install on a second
device if one exists; otherwise only after C passes and never with unsent
drafts or kept recordings, keeping Build 4 as the TestFlight fallback while it
is unexpired (uploaded 2026-09-03; the usual 90-day expiry is UNVERIFIED for
this build). Pointing Forey Test at production is blocked by
`assertTestApiUrl` and must not be worked around.

**C — Build 4 against the new backend (right after D-1, before any new
build).** 1 cold open with no forced logout (proves `JWT_SECRET` untouched);
2 Home (me, jobs, admin stat cards, review stack); 3 jobs list, detail, budget
summary, labour rollup; 4 expense parse and save, list, detail (real entry, or
the labelled test expense of `testflight-build.md` T-8 created and deleted —
founder decision); 5 labour records, summary, workers, an entry; 6 review
queue; 7 PDF report and Excel export; 8 Diagnostics shows `6036491` and the
production host; 9 background 60+ minutes, resume, silent refresh; 10 optional
logout and login.

**Does not transfer from Forey Test:** any file between 26,214,401 and
52,428,800 bytes; the 300 s timeout; the local storage adapter (Forey Test
never exercised S3); a fresh-install keychain; `APP_ENV=test`; the test
database's history and data.

## 9. UNVERIFIED register

| Item | Status / how it is settled |
|---|---|
| Installed daily build on the phone | Diagnostics (P-7) — founder checking |
| Build 4 still installable from TestFlight as the fallback | Founder checking |
| `APP_ENV`, env-file loading, effective cap, token lifetime | **Settled by P-3** (section 1) |
| Production credentials: HEAD 404 vs 403, bucket reachable | **Settled by P-4** for HEAD in `evidence/` |
| Production storage operations and permissions (create/part/complete, HEAD, copy, delete, get, abort) | **Settled by R1-b** |
| The new adapter's own code paths in production (classification, cancellation, supervised clean-up) | S-1 on the deployed `bcc3964` |
| Clean-up after a hard process death mid-upload | Not verified; no documented lifecycle rule — manual check and exact reclaim (7.2) |
| Lifecycle rules, incomplete multipart uploads | **Settled by P-5**: none, 0 |
| Completed staging objects in the bucket | Not listed — a listing is a founder decision |
| Row counts, PostgreSQL version | **Settled by P-6** |
| Old code on the new schema; the new code's normal path | **Settled by R1-a** (synthetic data, local adapter) |
| Whether Tigris honours `AbortIncompleteMultipartUpload` | Not documented by Tigris — treated as unsupported (7.2) |
| Production credentials carried in the image's env files | **Checked for current production components** in both env files, every line including comments: none found. Not proof of safety; one non-production credential category found; the `.env*.example` templates were not checked; values read in process only, never output, copied or hashed (section 10) |
| Downtime while the single machine is replaced | Fly documentation, or observed at D-1 |
| Whether the app's INFO startup logs reach Fly logs | The plan does not depend on it |
| Data retention across a TestFlight downgrade | A device test on a non-daily install |

## 10. Env files in the deployed image — two separate risks

The deployed image contains untracked local env files (`.env`-style, from the
working copy it was built from; `backend/` has no `.dockerignore`). Nothing
here says they are safe, and nothing says anything has leaked.

| Risk | What is established | What is not |
|---|---|---|
| **Runtime configuration** — the files changing how production behaves | With `APP_ENV=staging` the loader reads only `.env.staging`, which is absent; P-3 confirmed no env file is loaded. Today the files do not configure production. A different `APP_ENV`, or a future image carrying a matching file, would change that — the clean build in P-9 removes the files from the next image. | — |
| **Credentials carried in the image** — the files containing secrets | The env check (2026-10-01, `/app/.env` and `/app/.env.development`, read and compared inside the process; only names, categories and booleans printed): the active lines hold a handful of variables — a database URL **with a password** whose host is **loopback** (taken to be a local development database), a JWT secret that is **placeholder-like**, and plain configuration; the rest are comments. **No line, comments included, contains any current production credential component** — secret values (JWT secret, storage secret key, database password as stored and URL-decoded) and identifiers (storage key id, database user) reported separately, all negative — and none contains the production database host. Anyone who can read the image or open a shell in the container can read the files; the v29 image remains the rollback target until replaced. | Whether the loopback database password is used anywhere else; the `.env*.example` templates in `/app` and any nested files (not checked); the values themselves (read in process only; never printed, copied, stored or hashed) |

**Classification:** production credentials — none found by component
comparison; a non-production (local development) database credential — present.
That is not proof of safety: a credential reused elsewhere would not be
recognised by a comparison against production.

**Method limits:** exact substring matching of each current production value
(the database password also URL-decoded), so a split, partly changed,
differently encoded or previously rotated credential would not match; length
gates (secret 8, identifier 3, host 4 characters) — every production component
was long enough to be compared; "placeholder-like" means membership in a fixed
short list; "local development" is inferred from a loopback host, and a local
tunnel to a remote database would look the same.

Next steps are removal, not rotation: the clean build (P-9) leaves these files out of the next image, and
a `.dockerignore` is a later code slice. Rotating the local development
database password, if it is reused anywhere, is the owner's decision.

## 11. Known limits carried into the release

- `PUT` is not in the CORS allow-list — affects only a future browser uploader,
  not the mobile app.
- Rebuilds are not bit-reproducible (base image and uv unpinned) — the image
  digest is the reference.
- Completed staging objects have no cleanup tool (7.2).
- Incomplete multipart uploads have no automatic clean-up: Tigris documents no
  rule for it; the manual check and exact reclaim in 7.2 are the route.
- Review-queue resolve and reject were not exercised in the rehearsal (the API
  creates no review item from the synthetic inputs used), nor was user invite
  on the migrated schema; the new code's unassigned-capture, job-assignment
  and attachment-reset paths were not exercised either.

## 12. Decisions

**Taken (2026-10-01):** read-only checks P-2 to P-6 approved and done; the
migration runs on a one-off, no-service machine from the new image, migrate
first, then the backend, then — only after the daily Forey is confirmed — the
new app; the rehearsal uses synthetic data, not production data; production
storage writes are verified with labelled test objects, never with the first
real capture; incomplete uploads and completed staging residue are treated
separately, and no Expiration rule covers `evidence/`. R1-a, R1-b (with exact
clean-up of its own test objects) and the env-file check approved and done. No
bucket lifecycle change.

**Still needed:**

1. **Gate 2 procedure** — approve the manual check and exact-reclaim
   procedure (7.2), its schedule, whether completed staging residue is
   counted periodically (a listing), and acceptance of the residue the reclaim
   cannot remove (completed staging objects; incomplete uploads of legacy rows
   left `pending`).
2. **The release package** (section 3: backup, one-off migration machine,
   deploy, D-2, S-1, then N-1) — a separate approval.
3. **Production write smokes** in D-2 and N-1: the labelled test expense
   (create, then delete), or real entries only.
4. **Devices** — the installed build and Build 4's availability (founder
   checking).

## 13. R1 — pre-release verification (executed 2026-10-01)

Nothing here migrated, deployed, built an app or changed a secret or a bucket
policy. The plan as approved is kept below; the results follow each part.

### R1-a — synthetic rehearsal (local)

| | |
|---|---|
| Resources | The operator's existing local PostgreSQL container (port 5433), in a **new scratch database** created for this and dropped after (never the shared test database). Clean git worktrees at `6036491` and `bcc3964`. Local uvicorn processes. Docker Desktop was stopped when this was planned; starting it is a shared local runtime, so it is part of this approval. (As run: Docker Desktop was started and the existing `sitetracker-db` container ran under it; both were left running.) |
| Cost | None |
| Writes | The scratch database only. Nothing reaches Fly, Tigris or production. |
| Steps | 1 Migrate the scratch database to `c7d8e9f0a1b2` with `6036491`'s Alembic. 2 Bootstrap the first admin with `6036491`'s `scripts.seed_admin` and synthetic credentials — the API cannot create the first account (`/users/invite` requires an admin, and migrations seed none). Before running it, confirm the `DATABASE_URL` it will use names the scratch database and nothing else. Then, logged in as that admin, seed the rest through `6036491`'s own API (jobs, categories, suppliers, expenses, labour, a review-queue item). 3 Upgrade to `d9e0f1a2b3c4` with `bcc3964`'s Alembic. 4 Run the `6036491` app against it and script Build 4's calls, reads and writes. 5 Run the `bcc3964` app (local storage adapter in a temporary folder) and exercise the normal paths: Site Log declare, upload, finalize, list, read; evidence read and download. 6 Record the attempt-counter state and whether a downgrade would now be lossy. 7 Drop the scratch database. |
| Stop if | Any step-4 call fails on the new schema (migrate-first is then invalid — re-plan), or a step-5 normal path fails. |
| Undo | Drop the scratch database; remove the worktrees. |
| Gap it leaves | S3 (the local adapter stands in) — covered by R1-b and S-1. Real-data volume — not relevant: the altered table has 0 rows in production, and the old code's tables are unchanged. |

**R1-a result — passed.**

| Step | Result |
|---|---|
| 1 Migrate to `c7d8e9f0a1b2` (old Alembic) | Passed; `app` resolved to the clean worktree |
| 2 Bootstrap + seed through the old API | Admin bootstrapped after confirming the target was the scratch database; a job, alias, category budget, supplier and alias, worker, labour entry, org settings, expense and a contributor (through user invite, on the old schema) created. The one attempt to create a review item, an expense without a job, is refused by the API by design (it asks which job). Other inputs that produce a review item (the parser's supplier, category, amount or duplicate uncertainty) were not tried, so the review queue stayed empty and review resolve/reject were **not exercised** |
| 3 Upgrade to `d9e0f1a2b3c4` (new Alembic) | Passed, about 1 s including start-up; column present, counters 0 |
| 4 Old app on the new schema — Build 4's calls | **46 of 46** scripted calls returned the expected status, reads and writes, including the expense text parse (200): 43 on Build 4's endpoints, covering 39 of its 42, plus three calls Build 4 does not make (job audit, expense audit, supplier rename). Not exercised on the migrated schema: review-queue resolve and reject, and user invite (run only on the old schema, during seeding). The driver's own tally was 45 of 46: one call was first sent with the wrong parameter form and re-run as Build 4 sends it: 200. No application error |
| 5 New app — normal paths | **23 of 23**. 15 Site Log and evidence calls: Site Log declare, replay, upload, finalize, list (twice), read (event `complete`, inline text row flagged); bound evidence read and download with matching bytes; `link-job` on bound evidence refused 409; legacy evidence upload, read, download; job evidence and job Site Log lists. 8 daily-flow calls: login and seven reads. Only job-assigned captures were declared; unassigned capture, assign-job, relink-job, the unassigned list and attachment reset were not exercised |
| 6 Counters | 2 attachments with `upload_attempt_no > 0` — a downgrade would now be lossy |
| Extra — old app after the new code wrote | **11 of 11** sampled reads; no application error (the rollback case) |
| 7 Clean-up | Scratch database dropped; worktrees, synthetic files and credentials removed; other databases untouched. Docker Desktop and the `sitetracker-db` container were left running |

Not covered: the S3 path (local adapter), real-data volume, review-queue
resolve and reject, user invite on the migrated schema, and the new code's
unassigned-capture, job-assignment and attachment-reset paths. Logs and driver
output are in the private ops journal.

### R1-b — production upload-chain test with labelled test objects

| | |
|---|---|
| Where | Inside the running production machine (v29), from its existing virtualenv, `python -B`; no restart, no install. The production credentials stay in the process and are never printed. |
| Operations | The exact S3 sequence the new adapter performs, raw: `CreateMultipartUpload` on `evidence/<T1>/.staging.a1` (object metadata labels it as a release test), two `UploadPart`s (5 MiB + 1 MiB), `CompleteMultipartUpload`, `HeadObject` on the final key `evidence/<T1>/<sha16>.a1` (expect 404), `CopyObject` staging → final, `DeleteObject` staging, `GetObject` final with SHA-256 read-back, `HeadObject` final (expect 200). The same for a single-part object at `<T2>`. Then `CreateMultipartUpload` + one `UploadPart` at `<T3>` and `AbortMultipartUpload`, followed by `ListMultipartUploads` under `evidence/<T3>/` (expect 0). `T1`–`T3` are fresh uuid4s; every key is printed and recorded. **As run:** the abort was confirmed by `ListParts` on its exact upload ID (`NoSuchUpload`); `ListMultipartUploads` ran in the final check, under all three test prefixes. HEADs were added around every step: all five keys before any write, staging after complete and after delete, each final after its delete, and all five keys at the end. |
| Writes | Two final test objects (about 6 MiB and 1 KiB) and their staging objects, deleted by the sequence and then by the authorised exact clean-up (founder choice: test objects are not kept). The aborted upload leaves nothing. **No database row.** |
| Cost | About 30 requests planned; about 40 as run, with the added HEADs and checks. About 6 MiB stored for minutes — under one US cent at Tigris's published rates (rates not re-checked). |
| Stop if | Under 150 MB of memory available; any non-2xx on a write operation; the pre-copy HEAD is not 404; read-back SHA-256 mismatch; anything left after the abort. Stop at the first failure — no retries in a loop; bounded clean-up of this run's registered resources only. |
| Undo | No database change. Exact deletion of this run's final objects, verified absent. |
| Proves / does not prove | Proves the production credentials can perform each storage operation the new adapter uses, in `evidence/`, with read-after-write for copy and get. Does **not** prove the new adapter's own code paths (classification, cancellation, supervised clean-up) — S-1 runs them on the deployed `bcc3964` before the new app — nor any clean-up after a hard process death. |

**R1-b result — passed; everything this run created was removed.** Keys were
generated and recorded before any write; every registered key was confirmed
absent first; upload IDs were recorded as each upload was created.

| Step | A (6 MiB, 2 parts) | B (1 KiB, 1 part) |
|---|---|---|
| Create multipart (labelled) | ok | ok |
| Upload parts | 5,242,880 + 1,048,576 bytes | 1,024 bytes |
| Complete | ok | ok |
| HEAD staging | 200, 6,291,456 bytes | 200, 1,024 bytes |
| HEAD final before copy | 404 | 404 |
| Copy staging → final | ok | ok |
| Delete staging, then HEAD | 404 | 404 |
| GET final, SHA-256 read-back | match, 6,291,456 bytes, label present | match, 1,024 bytes, label present |
| HEAD final | 200 | 200 |
| Exact delete of final, then HEAD | 404 | 404 |

Abort test C: create, one part, `AbortMultipartUpload` — then `ListParts`
answered `NoSuchUpload`. Final check: all five registered keys absent; no
incomplete upload under any of the three test prefixes.

**Follow-up, read-only (2026-10-01):** bucket versioning was never enabled and
`x-tigris-enable-snapshot` is false; under each of the three test prefixes
there are 0 object versions, 0 delete markers, 0 current objects and 0
incomplete uploads. Nothing from this run remains in the bucket. Keys, upload
IDs, sizes and checksums are in the private ops journal (local, outside this
repository).

The release itself (B, M, D, S, N, C in section 3) is a separate approval.
