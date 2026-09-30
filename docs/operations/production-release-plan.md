# Production release plan — Site Log (`bcc3964`)

**Status: PREPARATION ONLY. Nothing in this document has been executed
against production.** It is the plan the founder approves or rejects, step
by step. Every stateful step below needs its own explicit approval; the
read-only checks it proposes need approval too, because they run on the
production machine.

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
| Upload cap secret | none (server default 25 MiB applies, *if* no env file supplies one — see P-3) | unchanged |
| `APP_ENV`, storage backend, bucket/endpoint values | **UNVERIFIED** (secret values are not read) | unchanged |

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
| **Old code (`6036491`) on the new schema** | Works | `6036491` has no API or service that reads or writes any Site Log table; its ORM does not map the new column, and ORM selects name their columns. An insert would take the server default 0. **Code inspection only — never run.** |
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
| **P-1** | Record the rollback target: current release (v29), its image reference and digest, the machine, and the kill signal/timeout. Confirm the rollback command against `flyctl releases rollback --help`; if unsupported for Machines, rollback is `flyctl deploy --image <v29 ref>`. | Written in the ops journal | Live release is not v29 or the image differs from the measured one (drift since 2026-10-01) |
| **P-2 (approval)** | Re-read the database revision immediately before the window, the same way it was measured: `MigrationContext` in a `READ ONLY` transaction, from the image's `/app/.venv`, `python -B`. **Never `uv run`** (it re-syncs packages inside the production container). | Exactly one revision, `c7d8e9f0a1b2` | Anything else. Do not install, sync or repair — report |
| **P-3 (approval)** | Effective configuration without printing secrets: in the container, print `APP_ENV` (not a secret), whether an env file is being loaded, and booleans only — storage backend is `s3`; `EVIDENCE_S3_BUCKET` equals the Tigris-attached bucket name; the S3 endpoint equals the Tigris endpoint; `EVIDENCE_MAX_UPLOAD_BYTES` absent; effective cap is 26,214,400; the access-token lifetime. **Do not rely on the `settings_loaded` startup log line** — the app's INFO logs are very likely not emitted (no logging configuration; uvicorn leaves the root logger at WARNING). | `APP_ENV` is `staging` or `production`; no env file loaded; `s3`; cap 26,214,400 | An env file is loaded, or `APP_ENV=development`: the new image is built clean (P-9) and would silently drop those settings. **Founder decides.** |
| **P-4 (approval)** | Storage Gate 1 probe — section 7.1 | `head_bucket` 200 **and** `head_object` 404 | Any 403/301/400, a 404 on the bucket, or 5xx/timeout (inconclusive, not a pass) |
| **P-5 (approval)** | Storage Gate 2 reads — section 7.2 | Counts and lifecycle rules recorded | An Expiration rule covers `evidence/` (a retention hazard, DEC-EVIDENCE-001) |
| **P-6 (approval)** | Row counts only, in a `READ ONLY` transaction: the five Site Log tables, `evidence`, and core tables (jobs, expenses, users, suppliers); `server_version_num`. | Server ≥ PG 11; attachment count recorded (expected 0 — `6036491` has no writer) | PG < 11, or unexpected Site Log rows — re-plan the lock window |
| **P-7** | Client inventory: the phone's Settings → Diagnostics shows commit `6036491`. | `6036491` | Any other build — redo the old-app compatibility check for that build first |
| **P-8 (approval)** | Rehearsal off production — see "Rehearsal" below. | Timings and results recorded | Old code fails on the new schema |
| **P-9** | Build the target image **once**, from a **clean worktree at `bcc3964`** (tracked files only; `backend/` has no `.dockerignore`, so building from a working copy copies untracked local files such as `.env.*` into the image). Build-only and push (e.g. `flyctl deploy --build-only --push`; dated example — confirm with `--help`). Record the image digest, base-image digest and uv version (`python:3.12-slim` and `pip install uv` are not pinned). | Digest recorded; build context contains no `.env*` except `*.example`, no `.venv`, no `var/` | Build fails or the context holds untracked env files |

**Rehearsal (P-8).** Two claims are code inspection only: that old code runs
on the new schema, and how long the migration's lock lasts. Do **not** use the
`6036491` test suite for the first: its fixture drops the schema and rebuilds
it from `6036491`'s own models, so it would test the old schema and pass
falsely. Instead: upgrade a disposable database to `d9e0f1a2b3c4` with Alembic,
run the `6036491` app against it, and script Build 4's calls; separately, time
the upgrade and downgrade on a disposable restore of a production backup
(R-3 to R-7 of `staging-backup-restore.md`, never the live cluster). Both are
optional founder decisions; without them the migrate-first order rests on code
inspection, and this document says so.

### Backup — immediately before the first write

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **B-1 (approval)** | Native backup — Gate R-1 unchanged. | Backup ID with status completed; its stop time is the restore point | Pending or failed — do not migrate |
| **B-2 (approval)** | Portable dump — Gate R-2 unchanged (three-guard `pg_dump`), plus dump-time row counts. Path A (portable dump) is the only restore path ever exercised (2026-06-02). | `pg_dump` exit 0, SHA-256 recorded, `PGPASSWORD` cleared, dump outside the repo and OneDrive | Any guard fires |

### Migrate first, while v29 keeps serving

| Step | Action | Proven by | Stop if |
|---|---|---|---|
| **M-1 (approval)** | `cd /app && python -B -m alembic upgrade d9e0f1a2b3c4` — explicit revision, never `head`, never `uv run` — **from the new image** (the v29 image has no `d9e0f1a2b3c4` script). How it runs is a founder decision (section 11). | `alembic current` (new image) shows `d9e0f1a2b3c4`; a read-only count of `upload_attempt_no > 0` is 0; attachment count equals P-6; no 5xx from v29 | The migration errors (it rolls back as one transaction — verify the revision is still `c7d8e9f0a1b2`), or a lock wait longer than a few seconds |
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
| **S-1 (approval)** | Production has **never** written to S3: no client at `6036491` reaches the writer, and its adapter hid HEAD errors. Gate 1's HEAD probe proves only the precondition. Choose one: a write-then-delete probe on a key outside `evidence/`; a watched first real capture; or explicit acceptance that write rights are unproven. Re-read the multipart count afterwards. | Write, get, staging delete succeed; multipart count unchanged | Any 403/5xx, or staging residue left. Keep the new app unbuilt; the backend can stay (Build 4 never touches storage) |

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

The only transient resource this plan contemplates is the optional one-off
migration machine (M-1), and only if the founder chooses it.

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

**What it cannot prove, and production still lacks:** the production key's
role and policy scope; the production bucket's settings (lifecycle rules,
consistency or location options); existing incomplete uploads and residue; the
endpoint, region and bucket actually held in the production secrets; and write
rights of any kind — production has never performed an S3 write.

**An isolated-bucket result never closes a production gate.** Gate 1 closes
only on the production probe (7.1); Gate 2 only on production lifecycle and
multipart evidence, or an explicit founder acceptance of residue.

### 7.1 Gate 1 — minimal read-only probe with the production credentials (not run)

- **Where:** inside the production machine, `cd /app`, `python -B`, using the
  app's own configured client — the credentials never leave the VM.
- **Never through `exists()`:** at `6036491` it returns False for *any*
  exception, so a 403 would read as "absent" — a false pass.
- **Calls:** `head_bucket` on the configured bucket, then `head_object` on
  `release-probe/head-404/<random uuid4 hex>`. Every adapter key starts with
  `evidence/`, so the probe key cannot collide with evidence. Both are HTTP
  HEAD requests; nothing is written.
- **Prints:** the HTTP status and error code of each call, the client's region
  name, and two booleans (configured bucket equals the Tigris-attached bucket;
  configured endpoint equals the Tigris endpoint). No credentials, bucket name,
  endpoint, keys or listing. Never `str(exc)`.
- **Reading it:** 200 then 404 = pass. 404 on the bucket = bucket missing
  (a HEAD cannot tell a missing bucket from a missing key without it) — fail.
  403 on either = no list rights or bad signing — fail; with `bcc3964` every
  upload would then fail. 301/400 = misconfigured — fail. 5xx or timeout =
  inconclusive. 200 on the probe key = impossible by construction — stop.
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
a suffix. (The module docstring's claim that the lifecycle policy covers it is
true only for incomplete uploads.)

Read-only checks, each needing approval, printing counts and flags only:
`list_multipart_uploads` under `evidence/` (upload count, truncation, count
older than 24 h — no keys); `get_bucket_lifecycle_configuration` (per rule:
status, whether `AbortIncompleteMultipartUpload` is present and its days,
whether Expiration is present, the filter prefix). If the app key lacks these
rights, the operator reads the Tigris console. A listing that counts completed
staging objects is a bucket listing, so it is a separate founder decision.

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

| Item | How it is settled |
|---|---|
| Installed daily build on the phone | Diagnostics (P-7) |
| `APP_ENV`, env-file loading, effective cap, token lifetime | P-3 |
| Production credentials: HEAD 404 vs 403, bucket reachable | P-4 (7.1) |
| Production write rights (multipart, copy, delete, get) | S-1 — not settleable read-only |
| Lifecycle rules, existing multipart and staging residue | P-5 (7.2) |
| Row counts, PostgreSQL version | P-6 |
| Old code on the new schema; migration lock duration | P-8 rehearsal, or accept code inspection |
| Downtime while the single machine is replaced | Rehearsal on the test app, or Fly documentation |
| Whether the app's INFO startup logs reach Fly logs | A read of recent logs for `settings_loaded` (the plan does not depend on it) |
| Data retention across a TestFlight downgrade | A device test on a non-daily install |

## 10. Known limits carried into the release

- `PUT` is not in the CORS allow-list — affects only a future browser uploader,
  not the mobile app.
- Rebuilds are not bit-reproducible (base image and uv unpinned) — the image
  digest is the reference.
- Completed staging objects have no cleanup tool (7.2).

## 11. Decisions this plan needs from the founder

1. **How M-1 runs the migration** from the new image while v29 serves:
   (a) a one-off `--rm` machine in the app from the P-9 image, no service
   ports (recommended; transient resource); (b) from the operator's machine
   through `fly mpg proxy` with a clean checkout (the database password reaches
   the operator's shell); (c) a `release_command` in `fly.toml` (config and
   ADR 0003 change — separate PR); (d) accept deploy-first and a 500 window on
   the evidence and Site Log routes (Build 4 is unaffected, but the order is no
   longer evidence-safe).
2. **Approve the read-only production checks** P-2, P-3, P-4, P-5, P-6.
3. **S-1:** write-then-delete probe outside `evidence/`, a watched first real
   capture, or accept that write rights are unproven.
4. **Gate 2:** add an `AbortIncompleteMultipartUpload` lifecycle rule (a Tigris
   configuration change), or formally accept residue with no cleanup tool.
5. **P-8 rehearsal:** spend a disposable-cluster restore (billable; copies
   production data into a temporary cluster that is then destroyed) and a
   local old-code run, or accept code inspection for migrate-first.
6. **Production write smokes** in D-2 and N-1: the labelled test expense
   (create then delete), or real entries only; any Site Log smoke creates real
   records.
7. **Devices:** is a second iPhone or tester available; is Build 4 still
   installable from TestFlight as the fallback.
