# Production release R2 and N-1 — record (`bcc3964`)

**Status: the backend is released (R2, 2026-10-09) and the new daily app,
build 5, is installed and accepted (N-1, 2026-10-11; section 7).** This file
records what R2 and N-1 actually did and what they did not prove. It authorises nothing. Identifiers that must stay out
of a public repository — backup ID, dump file name and SHA-256, cluster and
machine IDs, image digest, test keys, upload IDs, business row counts — are
in the operator's private ops journal (`run-20261009`), not here.

The plan this executed is `production-release-plan.md`. "Production" is the
Fly app `sitetracker-backend-staging` (historical name; it carries real
business data and serves the daily Forey, `com.forey.app`).

## 1. Outcome

| | Before R2 | After R2 |
|---|---|---|
| Backend code | `6036491` (release v29) | **`bcc3964` (release v30)** |
| Database revision | `c7d8e9f0a1b2` | **`d9e0f1a2b3c4`** |
| Daily app on the phone | Build 4 (`6036491`) | Build 4 (`6036491`) — replaced by build 5 (`bcc3964`) in N-1, section 7 |
| Upload cap | 26,214,400 (code default) | unchanged |

The image was built once, locally, from a `git archive` of `backend/` at
`bcc3964` (tracked files only) and used for both the migration and the
deploy. On the running machine all 186 files under `/app` matched `bcc3964`
by git blob hash, with no env, credential or data files. Rollback target:
the v29 image, code only; the schema stays at `d9e0f1a2b3c4`, which v29
tolerates (rehearsed in R1-a, observed in M-2).

## 2. What ran, in order (times UTC)

All on 2026-10-09 UTC unless stated (Sydney is UTC+11, AEDT). Window 1:
09:50–11:20Z. Window 2: 11:23–12:08Z.

| Step | When | Result |
|---|---|---|
| R2-0 pre-flight (read-only) | ~09:50Z | Database URL names the backed-up cluster, `fly-db`, `fly-user`; server identity token recorded; revision `c7d8e9f0a1b2`; configuration as P-3; Site Log, `evidence` and `evidence_audit_log` tables 0 rows; Gate 2 counts 0 incomplete, 0 staging, 0 lifecycle rules |
| B-1 native backup | 09:52:11–09:52:20Z | Full backup, status `completed` |
| B-2 portable dump | 09:56:44Z start, file written 09:58:23Z | Run by the founder on the operator PC (password typed at a hidden prompt; cleared after). Verified without credentials: table of contents includes the core and Site Log tables; full read-through exit 0; data stream exit 0 |
| Backup comparison | live counts ~09:50Z (R2-0) and ~10:00Z (B-2v) vs dump-time counts | **Row counts equal on all 22 tables.** This rules out inserts and deletes that would change a count; it does not rule out updates to existing rows between the live reads and the dump |
| M-1 migration | ~10:03Z | One-off machine from the same image, no services, audited first (186/186 files). `SET LOCAL` lock_timeout 5 s, statement_timeout 60 s, idle_in_transaction_session_timeout 30 s; target identity re-checked inside the migration transaction. Committed in 0.37 s. After-check: revision `d9e0f1a2b3c4`, column and CHECK present, attachments unchanged, attempt counters 0, no migration session left. Machine destroyed after a guarded check |
| M-2 old code on the new schema | 10:03Z–11:12Z | Founder's phone list 1: "all good". Logs over the same span: 216 requests, 0 × 5xx |
| — | 11:14Z | Window 1 ended before D-1; stopped at a supported state (v29 on `d9e0f1a2b3c4`) |
| D-1 deploy | 11:24Z | Rolling replacement of the single machine. About 13 s between the old process stopping (11:24:32Z) and the new one serving (11:24:45Z). Post-deploy: 186/186 files = `bcc3964`; configuration unchanged; same database identity; revision `d9e0f1a2b3c4`; read-only ORM select of all 21 mapped classes OK, including the new column; `/healthz` 200 |
| D-2 old app on the new backend | app sessions at 11:47Z and 20:41Z (2026-10-10 07:41 Sydney) | Founder's phone list 2: "saved and all ok"; item 9 (still signed in after hours): "still signed in". Logs show the silent token refresh both times |
| T-8 labelled test expense | saved 20:41:38Z; deleted 21:02:50Z (2026-10-10 07:41 and 08:02 Sydney) | Unique per-run marker; pre-check none; exactly one match after the save. The founder's first "deleted" did not reach the server (no `DELETE` logged); the second did (204). Final state: `rejected` with its audit row, so excluded from totals and reports |
| S-1 new adapter against production storage | 20:46Z (2026-10-10 07:46 Sydney) | Founder's explicit go (outside window 2). Multi-part (≈6.5 MiB) and single-part (2 KiB) `put` through `S3EvidenceStorage`, read back through `open` with matching SHA-256, `exists` true, only the final object left each time; a deliberate source failure surfaced unchanged and the adapter aborted its own multipart upload. Clean-up: inventory persisted first, then exact deletes; all test prefixes empty, no versions or delete markers |
| G-2 Gate 2 after | 20:46Z, after S-1 | 0 incomplete multipart uploads, 0 completed staging objects, 0 lifecycle rules (test prefixes 0) |

**Log observation after the deploy:** 11:24Z to 21:14Z: 124 app requests
(116 × 200, 1 × 201 = T-8 save, 1 × 204 = T-8 delete, 6 × 401 = expired
access token followed by a successful silent refresh), **0 × 5xx**. The only
traceback was the trapped `passlib` "error reading bcrypt version" notice at
start-up, also seen in R1-a.

## 3. Deviations from the plan

- **Image reference.** `flyctl machine run` with `repo@sha256:…` was refused
  (flyctl doubled the digest into an invalid reference; nothing launched).
  The migration machine and the deploy used the image tag, and the resolved
  digest was verified equal to the pinned one before anything ran. A rollback
  should use the v29 tag the same way, then verify its digest.
- **Abort design** (founder-approved 2026-10-03): no signal is ever sent to a
  database backend — PgBouncer reuses server backends, so a PID-based cancel
  could hit business traffic. The abort destroys the migration machine and
  relies on the transaction's own limits, confirmed by a read-only observer.
  Not needed in this run.
- **Timing.** Window 1 ran out before D-1 and the release resumed in window 2.
  The T-8 save and S-1 happened the next morning; S-1 on the founder's
  explicit go.
- **Founder's own write during M-2.** A job was created and deleted 9 s later
  from the app at 10:32Z; the founder confirmed it was theirs. No job was left.

## 4. Not verified by R2

- **S-1 limits:** put cancellation (the `_supervise_cleanup` cancellation
  branch and grace deadline), transient/permanent/unclassified error
  classification, and any API or database upload path (`POST /evidence`,
  Site Log upload). The Site Log upload path has since run once with real
  files in the N-1 acceptance (section 7); `POST /evidence` has not.
- **Hard process death mid-upload:** clean-up after it is not verified. Only
  the manual check and exact per-object reclaim (plan 7.2) exist.
- **Old-app items not exercised on the migrated schema or the new code:**
  the workers screen, adding a labour entry (a write, not authorised),
  logout/login, review-queue resolve and reject, user invite.
- **Restore:** this dump was verified readable, not restored. Restoring into
  the live cluster has never been rehearsed.

## 5. First-week check plan (N-1)

**When it applies:** for 7 days from the day the new app (build 5 or later
from `bcc3964`) is installed on the daily phone — build 5 went on on
2026-10-10, so through 2026-10-16 — and additionally after any upload a user
reports as failed, stuck or missing.

**Who and when:**

| Who | Does what | When |
|---|---|---|
| Founder | Reports any capture that failed, stayed "uploading", or is missing after save, with the time | As it happens |
| Founder | Opens a Claude session and asks for the daily check | Once a day, evening (target 19:00 Sydney), days 1–7 |
| Claude | Runs the read-only checks below and reports counts and findings | In that session |

**What is checked (read-only, counts and IDs only, never record content):**

1. **Unfinished records:** Site Log attachments still `pending` more than 15
   minutes after their attempt started; attachments `failed`; events not
   `complete`; legacy `evidence` rows `pending` older than a day or `failed`.
2. **Incomplete multipart uploads** under `evidence/`, by age.
3. **Completed staging objects** (`evidence/{id}/.staging[.aN]`).
4. **Server logs since the previous check:** 5xx on Site Log and evidence
   routes, and upload refusals (for example the size cap).
5. **Each user-reported failure:** the state of that capture's records and
   any storage residue under its evidence IDs.

Items 2–3 use the existing read-only Gate 2 counter. Item 1 needs a small
read-only state query that does not exist yet; it will be shown to the
founder for approval before its first use. It is not part of this record.

**Anomaly handling:**

- Report first: counts, affected capture or evidence IDs, and the exact keys
  and upload IDs (the identifiers go to the private ops journal, not chat or
  the repository).
- **Nothing is deleted or aborted automatically.** Every abort or delete
  needs the founder's approval of the exact list of objects, under plan 7.2
  (exact key and upload ID, never by prefix, only when the owning row shows
  the attempt is no longer live).
- A Site Log attachment left `pending` cannot be recovered by retrying from
  the app alone: the app skips `pending` attachments and the server refuses
  their upload (409). Recovery is a sequence, approved per capture by the
  founder: the existing admin reset (allowed only after its 15-minute minimum
  age, with a reason), which marks the attempt `failed`; then a retry from the
  app, which uploads and finalizes; then a check that the event is
  `complete`. A legacy `evidence` row left `pending` cannot be reclaimed by
  the procedure and is residue accepted only by decision.
- A manual check is **not** automatic clean-up after a hard crash; that
  remains unverified.

## 6. Build 4 as the fallback

- **EAS record:** build 4 of `com.forey.app` (0.1.0), commit `6036491`,
  created 2026-09-03 00:25Z. Its EAS download expired on 2026-10-03; that is
  EAS file retention, not TestFlight.
- **TestFlight availability: NOT VERIFIED.** There is no App Store Connect
  read access from this side. TestFlight builds normally expire 90 days after
  upload, which would put build 4 at about 2026-12-02 — computed, not
  observed. In the N-1 acceptance the founder was asked to check Forey →
  Previous Builds; the answer was a blanket "all works fine" covering the
  whole list, with no screenshot, so build 4 being listed is founder-reported
  and not itemised.
- **How a fallback works:** TestFlight installs an earlier build over the
  current one. No uninstall is needed and the app's data stays (same bundle
  identifier). Build 4 has no Site Log, so any unsent Site Log drafts or kept
  recordings made in the new app would be unreachable from it; fall back
  only when the new app shows none. Whether such drafts survive a downgrade
  and a later re-upgrade is not verified.

## 7. N-1 — the new daily app (build 5)

**Built and checked before submission (2026-10-10 UTC):**

- Real Forey (`com.forey.app`) 0.1.0 (5), production profile, built by EAS
  from a clean checkout of `bcc3964` after a clean `npm ci`.
- Checks on that source: `tsc --noEmit` exit 0; jest 17 suites, 237 passed,
  8 skipped. There is no mobile job in CI.
- Checked inside the built IPA, not only in configuration: bundle identifier
  `com.forey.app`, version 0.1.0 (5); embedded app config with API
  `https://sitetracker-backend-staging.fly.dev`, upload cap 26,214,400 bytes
  with a 180 s upload timeout, build commit `bcc3964`, variant `default`; an
  App Store provisioning profile for the right team with no device list and
  no debugging entitlement; no Forey Test API host, 50 MiB cap or Forey Test
  identifier anywhere in the JavaScript bundle.
- Submitted to the real Forey's TestFlight by explicit build ID, not
  "latest". The EAS build and submission IDs and the IPA hash are in the
  private ops journal.

**Install:** the founder installed build 5 from TestFlight over the existing
app on 2026-10-10 (no uninstall, per the founder). The app stayed signed in:
no sign-in request reached the server, only silent token refreshes.

**Acceptance.** The founder ran the checks on the phone. The server side
comes from a continuous read-only log capture (2026-10-10 05:26Z to
2026-10-11 07:30Z, no gap longer than about 30 s after the four-content
entry) and the Gate 2 counter.

| Check | Evidence | Result |
|---|---|---|
| Still signed in; daily screens | founder; server log | On each app resume the expired access token was answered 401 and silently refreshed (200), then data loaded. Today, Jobs, Expenses, Labour, Workers, the review queue and the Excel export all answered 200 |
| Diagnostics shows `bcc3964` and the production API | founder | Reported OK, inside a blanket "all works fine" (not itemised) |
| Build 4 still listed in TestFlight | founder | Reported OK, inside the same blanket report (not itemised, no screenshot) |
| First Site Log entry, 2026-10-10 06:59Z | server log | Text only, saved `complete`; no file was uploaded. The founder continued past the step that said to stop if a voice note had been added, so it is recorded as text only by intent |
| Four-content entry, 2026-10-11 02:17Z | server log; founder | Declared, three file uploads accepted (201), finalised `complete`: text, photo, voice note and PDF |
| Viewing after leaving and reopening | server log; founder | The entry was reopened from the list at 07:29Z. Every stored file was served by the server at least once (200): one only in the visit right after saving, one only in the reopen visit, one in both. Founder: "all good". A first report of this check had no matching request on the server; it was repeated and reached the server at 07:29Z. Whether each file rendered is the founder's observation |
| ~28 MB file refused at pick time | founder; server log | Reported OK; no oversized upload reached the server |
| Storage after acceptance | Gate 2 counter, ~02:21Z | 0 incomplete multipart uploads, 0 completed staging objects, 0 lifecycle rules |
| Server errors | log capture | 0 × 5xx; no error-level lines |

**Not verified by N-1:**

- One phone and one account (the founder's iPhone, admin role). Android,
  a contributor account and a second device are untested on production.
- Weak-network and offline capture, retry and recovery on production.
- Hard process death during an upload, and the S-1 limits in section 4.
- The two checks covered only by the blanket report (Diagnostics, build 4
  listed).
