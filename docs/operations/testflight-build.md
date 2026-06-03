# TestFlight build & internal-test runbook (iOS)

Operator runbook for producing the first installable SiteTracker iOS build
and getting it onto the operator's own iPhone via Apple TestFlight, pointed
at the staging backend.

Decision of record: `docs/adr/0004-mobile-testflight-distribution.md`.
Backend/env decisions: ADR 0003 (staging deployment), ADR 0002 (env/secrets).

## Authoring principles (per ADR 0003)

- Commands below are **dated illustrative examples (2026-06-03), not canonical
  truth.** Provider CLIs (EAS, Fly, Apple) drift.
- Each gate states **intent + verification + back-out**. Intent and
  verification survive CLI churn; specific flags may not.
- **Before any state-changing gate, re-run the relevant `<command> --help`**
  and reconcile it with the example before proceeding.
- **Single-gate governance:** every gate is individually operator-approved.
  Gates are not bundled or pre-authorized.
- **Operator-driven.** Every Apple/Expo credential — enrollment, signing, the
  App Store Connect API key, an Expo access token — is handled by the
  operator. The assistant never holds or enters them.

## Terminology — keep these three distinct

1. **EAS internal distribution build** — an installable build delivered
   through EAS using ad-hoc provisioning (registered device UDIDs). This is
   NOT TestFlight, and it does NOT replace App Store Connect / TestFlight
   submission. (`mobile/eas.json` -> `build.preview`.) Optional; not the trial
   path.
2. **Apple TestFlight internal testing** — testers are App Store Connect users
   (<=100). Builds for internal testers do NOT go through Beta App Review.
   This is the chosen first path, operator only. "Company-internal" (a staff
   member of the business) is NOT the same as "Apple internal": only App Store
   Connect users are Apple internal testers.
3. **Apple TestFlight external testing** — up to 10,000 testers by email or
   public link. The first build sent to an external group goes through **Apple
   Beta App Review.** A site worker or family member who is NOT an App Store
   Connect user is an **external** tester even though they are company-internal.
   Do not open this flow until the operator's own install passes (T-8).

## API URL safety (highest technical risk)

The app falls back to `http://127.0.0.1:8000` if `EXPO_PUBLIC_API_URL` is
unset at build time (`mobile/src/api/client.ts`). A build that bakes localhost
installs fine but cannot work on cellular with the PC off.

- The staging URL lives in `mobile/eas.json`
  `build.*.env.EXPO_PUBLIC_API_URL`, so EAS cloud builds are deterministic.
- **T-3 verifies the backend is live before building.**
- **T-3 verifies the resolved config is the staging URL, not localhost.**

Staging URL: `https://sitetracker-backend-staging.fly.dev`

**Future hardening (not in this batch):** add a build-time guard that fails
the `production` build if the resolved API URL is localhost, so a
misconfigured build can never reach TestFlight.

## iOS build metadata & export compliance

`mobile/app.json` sets `ios.infoPlist.ITSAppUsesNonExemptEncryption = false`.
This declares the app uses only exempt encryption (standard HTTPS/TLS) and lets
the upload skip Apple's per-build export-compliance question. The operator must
confirm this is accurate for the current build before submission — it is an
operator compliance confirmation, not legal advice. If the app later adds
non-exempt cryptography, revisit this flag.

## Gates

### T-0 — Operator confirmations (BLOCKING)

Intent: confirm prerequisites exist before any build work.
- [ ] Apple Developer Program enrollment is ACTIVE (operator confirms; never
      assumed).
- [ ] Expo account exists and EAS access works.
- [ ] Staging backend is live:
      `curl -sI https://sitetracker-backend-staging.fly.dev/healthz` -> `200`.
- [ ] First test user = the operator only.
Verify: all four true.
Back-out: none (nothing changed). Do not proceed until all four hold.

### T-1 — Doc/config review

Intent: review and commit the doc/config batch (ADR 0004, `mobile/eas.json`,
`mobile/app.json`, `mobile/.env.staging.example`, this runbook).
Verify: diff reviewed; no secrets; reviewer-approved; committed.
Back-out: `git revert` the doc/config commit (docs/config only — reversible
via git).

### T-2 — eas init / projectId (operator; stateful)

Intent: create the Expo project and write `extra.eas.projectId` into
`mobile/app.json`. The projectId is NOT hand-written; `eas init` generates it.
Pre: re-run `npx eas-cli init --help`.
Illustrative: `npx eas-cli login` -> `npx eas-cli init`
Verify: `mobile/app.json` now has `extra.eas.projectId`;
`npx eas-cli project:info` shows the project.
Back-out: remove the `extra.eas.projectId` line; delete the project in the
Expo dashboard.

### T-3 — Backend URL verification (API URL safety)

Intent: prove the build will target staging, not localhost, against a live
backend.
- Liveness: `curl -sI https://sitetracker-backend-staging.fly.dev/healthz`
  -> `200`; a login smoke (`POST /auth/login`) returns a token.
- Config:
  `EXPO_PUBLIC_API_URL=https://sitetracker-backend-staging.fly.dev npx expo config --type public`
  shows `extra.apiUrl` = the staging URL.
- Confirm `mobile/eas.json` `build.production.env.EXPO_PUBLIC_API_URL` = the
  staging URL.
Verify: all three pass.
Back-out: read-only. If liveness fails -> STOP and fix the backend first
(`docs/operations/staging-deploy.md`). Do not bake an unverified URL.

### T-4 — EAS iOS build (operator; stateful, provider)

Intent: produce a signed store-distribution iOS build (TestFlight-eligible)
via EAS cloud.
Pre: re-run `npx eas-cli build --help`. EAS prompts for Apple login and
manages iOS distribution signing during this step (operator).
Illustrative: `npx eas-cli build --platform ios --profile production`
Verify: build succeeds; build logs show `EXPO_PUBLIC_API_URL` = the staging
URL; the artifact is a store build (not internal-distribution).
Back-out: discard the build; nothing is on App Store Connect until T-6.

### T-5 — App Store Connect app record / signing (operator)

Intent: ensure an App Store Connect app record exists for
`com.sitetracker.mobile` and distribution signing is valid.
- Register the bundle id if needed and create the ASC app record.
- Confirm EAS-managed distribution cert + provisioning profile
  (`npx eas-cli credentials`).
Verify: the app record is visible in App Store Connect; credentials show a
valid iOS distribution profile.
Back-out: remove the app record (operator, in App Store Connect).
Operator-only: the assistant cannot create ASC records or handle signing.

### T-6 — Submit to Apple TestFlight (operator; stateful, provider)

Intent: upload the T-4 build to App Store Connect -> Apple TestFlight and
assign INTERNAL testers (operator only, first).
Pre: re-run `npx eas-cli submit --help`. **Do NOT run `eas submit` while the
`REPLACE_WITH_*` placeholders remain in `mobile/eas.json`** — first replace
`submit.production.ios.ascAppId` and `appleTeamId` with real,
operator-confirmed values (these are identifiers, not secrets; a leftover
placeholder makes submit fail or target nothing). Provide the App Store
Connect API key out-of-band (EAS prompt / EAS-managed) — never commit it.
Illustrative: `npx eas-cli submit --platform ios --profile production`
Verify: the build appears in App Store Connect -> TestFlight and finishes
processing; it is assigned to Apple internal testers (operator).
Reminder: internal testers (ASC users) -> no Beta App Review. Adding non-ASC
company staff later = external testers -> first external build -> Beta App
Review. Do NOT open the external flow here.
Back-out: stop testing / expire the build in App Store Connect.

### T-7 — Install on phone (operator)

Intent: install the build through the TestFlight app on the operator's iPhone.
Verify: the app installs and launches.
Back-out: delete the app from the device.

### T-8 — 4G + PC-off smoke test (operator) — ACCEPTANCE

Intent: prove real cellular use with the development PC OFF.
1. Turn the development PC OFF.
2. Phone on cellular (4G/5G); Wi-Fi OFF.
3. Open the installed TestFlight build.
4. Log in with staging credentials.
5. Create/capture ONE test expense (clearly labelled test data).
6. Confirm the backend received it (the expense appears on re-fetch / in the
   staging data).
7. Mark or delete the test data per the Beta data policy below.
Verify: every step passes; the expense persisted on staging with the PC off.
Back-out: delete the test expense.

### T-9 — Close-out

Intent: record the outcome (status only).
- Record: build number, TestFlight processing result, smoke pass/fail,
  defects.
- On pass: the Apple-internal path is proven for the operator. External
  testers remain a separate, later, Beta-App-Review-gated step.
- Do NOT embed staging credentials, ASC keys, tester PII, or other secrets /
  specific evidence values in this doc.
Verify: a one-line close-out status is recorded.

## Beta data policy (test-data hygiene)

- Use clearly labelled test data (e.g. `TEST — TestFlight smoke`).
- Delete or mark the test expense after the smoke so trial data stays clean.
- No real financial data until the trial is formally opened.

## What stays operator-only

Apple Developer Program enrollment; all Apple/Expo credentials — signing
certs, provisioning profiles, the App Store Connect API key, app-specific
passwords, Expo access tokens; `eas init`; `eas build`; `eas submit`; the App
Store Connect app record and tester management; the live `/healthz` and login
checks; and the on-device smoke. The assistant drafts and verifies docs/config
only — it never runs these or handles credentials.

Signing assets (`*.p8`, `*.p12`, `*.key`, `*.mobileprovision`, `*.jks`) are
gitignored and must never be committed.
