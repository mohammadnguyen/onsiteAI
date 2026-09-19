# Forey Test — build and install alongside Forey (iOS)

Operator runbook. Companion to `docs/operations/testflight-build.md`, whose
gates, credential rules and operator-only steps all still apply.

Decision of record: `docs/decisions/ADR-005-forey-test-app-variant.md`
(ACCEPTED as an architecture; creating resources, signing, building and
submitting remain separate operator decisions).

## What this produces

A second app on the same iPhone, `Forey Test`, that cannot see or disturb
Forey's data and talks only to the test backend. Forey stays installed,
signed in, and untouched.

## Authoring principles

Commands are **dated illustrative examples (2026-09-19), not canonical
truth**; re-run `<command> --help` before any state-changing step. Every
Apple and Expo credential is handled by the operator and never by the
assistant.

## Where the API URL comes from — read this before building

A cloud build does **not** inherit the operator's shell. `app.config.ts` is
evaluated on the EAS build machine, so `EXPO_PUBLIC_API_URL` must be part
of the build profile:

`mobile/eas.json` -> `build.test.env.EXPO_PUBLIC_API_URL`.

It ships **empty**, and the config throws on an empty, non-https or
protected-host value, so a test build cannot be produced until the address
is filled in, and can never silently fall back to the real backend. Fill it
in once, commit it, and both the local check and the cloud build read that
one value. Do not pass the URL as a shell variable for the build: it would
work locally and be missing in the cloud.

`build.production` and `build.preview` are untouched and still point at the
existing backend.

## Preconditions (operator confirms; nothing below runs until all hold)

- [ ] The test backend exists and **all five** verification steps in
      `docs/operations/forey-test-backend.md` pass - including the capture,
      the upload, the finalize and the read-back, not only `/healthz`.
- [ ] `mobile/eas.json` -> `build.test.env.EXPO_PUBLIC_API_URL` is the test
      API's https address, committed.
- [ ] Apple Developer Program enrollment is active (team `W58T3X33VM`).
- [ ] An App Store Connect app record exists for **`com.forey.app.test`**,
      and its App ID is in `mobile/eas.json` -> `submit.test.ios.ascAppId`,
      replacing `REPLACE_WITH_FOREY_TEST_ASC_APP_ID`.
- [ ] EAS access works from the operator's machine.

## Gates

### FT-1 — Config verification (read-only; the assistant may run this)

Intent: prove the test variant is separate, that it reads the same URL the
cloud will, and that the default app is unchanged.

```bash
cd mobile

# The default app: must be Forey / com.forey.app / forey.
npx expo config --type public

# The test app, using THE VALUE THE CLOUD WILL USE - read from eas.json,
# not retyped. Must be Forey Test / com.forey.app.test / foreytest, with
# extra.apiUrl equal to the test API.
FOREY_VARIANT=test \
EXPO_PUBLIC_API_URL="$(node -p "require('./eas.json').build.test.env.EXPO_PUBLIC_API_URL")" \
npx expo config --type public

# And the refusals, which must all throw:
FOREY_VARIANT=test npx expo config --type public                     # empty
FOREY_VARIANT=test EXPO_PUBLIC_API_URL=https://sitetracker-backend-staging.fly.dev \
  npx expo config --type public                                      # the real backend
FOREY_VARIANT=test EXPO_PUBLIC_API_URL=https://sitetracker-backend-staging.fly.dev:443 \
  npx expo config --type public                                      # same host, port form
```

Verify: the default unchanged, the test variant correct, all three
refusals. Back-out: read-only.

### FT-2 — Test API liveness (read-only)

Re-run section (a) and (b) of the verification in
`docs/operations/forey-test-backend.md` against the address now committed
in `eas.json`. Note that the health check is a GET (`curl -sf .../healthz`):
`curl -I` sends HEAD and returns 405 from a healthy deployment. A 404 on `/site-log-events/mine`, or a failure at the
upload or finalize step, stops the build.

### FT-3 — Build (operator; stateful, provider)

Pre: re-run `npx eas-cli build --help`. EAS will ask for Apple credentials
and create signing assets for the **new** bundle identifier; that is
expected and is an operator decision.

```bash
cd mobile
npx eas-cli login
npx eas-cli build --platform ios --profile test
```

No environment variables on that command line: everything the build needs
is in the profile.

Verify in the build log: `FOREY_VARIANT=test`, the test API URL, and bundle
identifier `com.forey.app.test`. If the log shows `com.forey.app`, stop -
that is Forey.

Back-out: discard the build. Nothing reaches TestFlight until FT-4.

### FT-4 — Submit ONE named build to the RIGHT record (operator)

Two ways to send the wrong thing: `eas submit` with no profile defaults to
`production`, whose `ascAppId` is **Forey's**; and `--latest` submits
whatever built most recently, which may be another profile's artefact.
Neither is used here. Name the profile, and name the build.

```bash
# Run these from the REPOSITORY ROOT, not from mobile/.

# 1. Find the build. --json prints the fields the human-readable output
#    leaves out; take `id`, `gitCommitHash` and `buildProfile`.
(cd mobile && npx eas-cli build:list --platform ios --profile test --limit 5 --json --non-interactive)
```

The other two checks are properties of the SOURCE that build came from, so
they are read out of that commit - never out of the working tree, which may
have moved on:

```bash
SHA=<gitCommitHash from step 1>

# 2. The API url that commit bakes into a test build.
git show "$SHA:mobile/eas.json" \
  | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).build.test.env.EXPO_PUBLIC_API_URL"

# 3. The identity that commit gives the test variant.
git show "$SHA:mobile/app.config.ts" | grep -n "bundleIdentifier\|config.name\|config.scheme"
```

Step 3 must show the test branch setting `com.forey.app.test`, `Forey Test`
and `foreytest`. FT-1, run at the head you are building from, is what
proves that block actually produces those values; step 3 proves the build
came from a commit that contains it.

Four things must match before you continue:

| Check | Where it comes from | Expected |
|---|---|---|
| Git commit | step 1, `gitCommitHash` | the SHA you intended to test |
| Profile | step 1, `buildProfile` | `test` |
| `EXPO_PUBLIC_API_URL` | step 2, from that commit | the test API, not the existing backend |
| Bundle identifier | step 3, from that commit | `com.forey.app.test` — **not** `com.forey.app` |

If step 2 prints the existing backend's address, or step 3 does not show
the test identity, that build is not a Forey Test build: stop.

```bash
# 3. Submit that exact build, to the test profile's target.
# From mobile/: eas submit loads the project config and the submit profile
# out of mobile/eas.json, which does not exist at the repository root.
(cd mobile && npx eas-cli submit --platform ios --profile test --id <build-id>)
```

Verify before confirming: the command prints the target App Store Connect
app. It must be the `com.forey.app.test` record. If
`submit.test.ios.ascAppId` still reads `REPLACE_WITH_FOREY_TEST_ASC_APP_ID`
the command fails - that is deliberate. If it prints Forey, stop.

Back-out: the build can be removed from TestFlight; Forey's own record is a
different app and is not modified by this.

### FT-5 — Confirm the isolation before testing anything else

- [ ] Both apps on the home screen: `Forey` and `Forey Test`.
- [ ] Forey still opens signed in, with its own data.
- [ ] Forey Test opens signed out.
- [ ] Forey Test -> Settings -> Diagnostics shows `FOREY TEST — test data
      only` and the **test** API host.
- [ ] Forey -> Settings -> Diagnostics still shows the existing host.
- [ ] Signing out of Forey Test does not sign out Forey.

## Device acceptance checklist (test data only)

1. Microphone permission **denied** - the app says so and stays usable;
   then **allowed** - recording works.
2. Photo permission denied, then allowed - same.
3. Record a voice note, save the entry, reopen it, play it back.
4. Attach a photo and a document; save; reopen; open each one.
5. Aeroplane mode: write an entry with an attachment and save - it must say
   the save result is unconfirmed and keep the draft.
6. Force-close the app and reopen: the text and the attachment are still
   there.
7. Restore the network and resume - exactly ONE record is created.
8. Start a capture, and while it is saving go back and start another - the
   first one finishing must not replace or clear the second.
9. **Upgrade, not reinstall**: with an unsent draft waiting, install the
   next Forey Test build over the top. The draft and its attachments must
   still be there. Do not delete the app to test this - deleting removes
   the Documents directory and destroys the very thing under test.
