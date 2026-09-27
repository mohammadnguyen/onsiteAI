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

It shipped **empty** and now reads `https://forey-test-api.fly.dev`, the
independent test backend. The config throws on an empty, non-https or
protected-host value, so a test build cannot be produced without an address
and can never silently fall back to the real backend. Both the local check
and the cloud build read that one committed value. Do not pass the URL as a
shell variable for the build: it would work locally and be missing in the
cloud.

`build.production` and `build.preview` are untouched and still point at the
existing backend.

## Preconditions

Two kinds, kept apart on purpose: what is committed in this repository and
can be read here, and what is true of an external account and has to be
confirmed by the operator each time.

**Already true in this repository** — verifiable by reading it, no tick
required:

- `mobile/eas.json` -> `build.test.env.EXPO_PUBLIC_API_URL` is
  `https://forey-test-api.fly.dev`.
- `mobile/eas.json` -> `submit.test.ios.ascAppId` is **6813764247**, the
  App Store Connect record for `com.forey.app.test`, created 2026-09-19.
  Forey's own record, 6799641228, appears only under `submit.production`.
- Every build profile states `FOREY_VARIANT`, and a cloud build with it
  missing or disagreeing with the profile fails rather than producing a
  `com.forey.app` binary.

**Operator confirms each run; nothing below starts until all hold:**

- [ ] The test backend is up AND its database is answering — FT-2, not just
      `/healthz`. It has died between sessions before.
- [ ] All verification steps in `docs/operations/forey-test-backend.md`
      pass, including the capture, the upload, the finalize and the
      read-back.
- [ ] Apple Developer Program enrollment is active (team `W58T3X33VM`), and
      the current Program License Agreement is accepted - an outdated one
      blocks submitting a new app, and only the Account Holder can accept it.
- [ ] The App Store Connect record is still the Forey Test one and nothing
      in the account has been renamed or re-pointed since the last run.
- [ ] EAS access works from the operator's machine.

## Gates

### FT-0 — Dependency integrity (read-only; the assistant may run this)

Intent: catch what `tsc` and Jest structurally cannot. Neither resolves a
native module, so a package tree that cannot produce a working binary
passes both and fails only on a device - as a white screen, because the
crash happens while the bundle is being evaluated and no error boundary
exists yet.

```bash
cd mobile
npm ci          # judge the lockfile the cloud builds from, not a local tree
npm run doctor
```

`npm ci` first, deliberately: the cloud installs from `package-lock.json`,
and a gate run against whatever happens to be in `node_modules` can pass
while the committed lockfile is broken. That is the exact shape of the
defect this gate exists for.

`npm run doctor` (`mobile/scripts/check-deps.mjs`) does two things:

| Step | Fails the gate? |
|---|---|
| Every autolinked native module installed exactly once — decided by `expo-module.config.json`, the same file autolinking reads | **Yes.** This is the defect that reached a device |
| `expo-doctor`, run as a second opinion | Only for its "required peer dependencies" and "no duplicate dependencies" checks. Its own exit code is ignored |

`expo-doctor`'s exit code is ignored because it cannot succeed here through
no fault of this project: under npm 11 two of its checks abort on
`npm explain` returning non-zero for a package that is simply absent. It
also reports a false positive about `app.json` (the dynamic config does
read it) and six patch-version mismatches that `main` shares and ships
with. Its output is printed for a human; the two checks above are read by
name.

It is pinned as a devDependency, so this downloads nothing and behaves the
same on any machine.

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
FOREY_VARIANT=test EXPO_PUBLIC_API_URL=https://sitetracker-backend-staging.fly.dev. \
  npx expo config --type public                                      # same host, trailing dot
FOREY_VARIANT=sideways npx expo config --type public                 # unknown variant

# Fail-closed on a cloud build, simulated locally. Both must throw:
EAS_BUILD=true EAS_BUILD_PROFILE=test npx expo config --type public  # variant unstated
EAS_BUILD=true EAS_BUILD_PROFILE=test FOREY_VARIANT=default \
  npx expo config --type public                    # test profile, real app identity
```

Verify: the default unchanged, the test variant correct, and **every**
refusal throwing. Back-out: read-only.

### FT-2 — Test API liveness (read-only)

**Run this at the start of every device session, not only when the
environment is created.** The database that died had passed the full
verification when it was fresh; it failed hours later, while idle, in the
middle of a device test.

`/healthz` does not touch the database, so it stays 200 while Postgres is
dead. One extra request settles it:

```bash
API=https://forey-test-api.fly.dev

curl -sf $API/healthz                                   # {"status":"ok"}

# A real query, with a deliberately wrong password.
#   401 = the database is answering.  500 = it is not.
curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"nobody@forey-test.example.com","password":"wrong"}'
```

A 500 here means the environment is down: go to the Recovery section of
`docs/operations/forey-test-backend.md` before doing anything else.

Then re-run sections (a) and (b) of that document's verification against
the address committed in `eas.json`. Note the health check is a GET;
`curl -I` sends HEAD and returns 405 from a healthy deployment. A 404 on
`/site-log-events/mine`, or a failure at the upload or finalize step, stops
the build.

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

Five things must match before you continue:

| Check | Where it comes from | Expected |
|---|---|---|
| Git commit | step 1, `gitCommitHash` | the SHA you intended to test |
| Profile | step 1, `buildProfile` | `test` |
| `EXPO_PUBLIC_API_URL` | step 2, from that commit | the test API, not the existing backend |
| Bundle identifier | step 3, from that commit | `com.forey.app.test` — **not** `com.forey.app` |
| `ascAppId` | `git show "$SHA:mobile/eas.json"`, `submit.test.ios` | **6813764247** — **not** 6799641228 |

If step 2 prints the existing backend's address, or step 3 does not show
the test identity, that build is not a Forey Test build: stop.

```bash
# 4. Submit that exact build, to the test profile's target.
# From mobile/: eas submit loads the project config and the submit profile
# out of mobile/eas.json, which does not exist at the repository root.
(cd mobile && npx eas-cli submit --platform ios --profile test --id <build-id>)
```

Verify before confirming: the command prints the target App Store Connect
app. It must be **Forey Test, App ID 6813764247**, the `com.forey.app.test`
record. If it prints Forey or 6799641228, stop - that is the real app.

Back-out: the build can be removed from TestFlight; Forey's own record is a
different app and is not modified by this.

### FT-5 — Confirm the isolation before testing anything else

- [ ] Both apps on the home screen: `Forey` and `Forey Test`, and they no
      longer look alike — Forey Test carries a red **TEST** band across its
      icon. Telling them apart must not require opening either one.
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
10. **Logout with unsent work**: with at least one unfinished capture,
    Settings -> Log out must WARN, name how many captures will be lost, and
    wait for an explicit confirmation. Cancel must leave you signed in with
    the draft intact. Confirm must sign out and remove only that account's
    drafts and files.
11. **Logout with nothing unsent** must log straight out, with no dialog.
12. **One saved record, read in the app.** Open a saved record that has
    text, a photo, a voice note and a PDF drawing:
    - the text appears once, as the body - NOT again as a "Text" attachment
      row (the server's own copy is hidden; a .txt you attached yourself
      still shows);
    - **View** on the photo shows it full screen; pinch to zoom; Close
      returns to the record;
    - **Play** on the voice note is AUDIBLE WITH THE RINGER SWITCH ON
      SILENT, shows elapsed / total time and a progress bar; Pause pauses;
      the label returns to Play when it ends;
    - **View** on the PDF shows the drawing in the app: scroll between
      pages, pinch to zoom, Close returns to the record;
    - **Close is reachable, both ends, every state.** The viewer has a
      Close at the top beside the title AND a full-width Close at the
      bottom. Check on the phone that reported it, in BOTH orderings:
      (a) a call already active (green in-call indicator showing) when
      View is tapped; (b) the viewer open first, then a call arriving.
      In each: the top bar must sit BELOW the status bar / Dynamic Island,
      and both Close controls must be visible and must close the viewer -
      for a photo, for a PDF, while a PDF is still loading, and after a
      failed load. Record which ordering was checked on which phone. If
      the only way out is force-quitting the app, that is the Build 6
      defect and a FAIL;
    - **Share** on any of them opens the share sheet - a second option,
      not what View does.
13. **List rows tell records apart.** In My site log, a record with no text
    shows what it holds ("1 photo · 1 document"), when, and which job; two
    records are never the same "(no text)".
14. **Nothing survives leaving the screen.** Start View on a large PDF and,
    while its row still shows the spinner, go back to the list. Nothing may
    open on the list, and no viewer or playing recording may appear when
    you return. What this exercises on a phone is the "screen still in
    front" check; the "same account" check cannot be reached this way
    (getting to Sign out means leaving the screen first) and is covered by
    the unit tests instead - session change during each await.
15. **A link inside a PDF goes nowhere.** Preparation, with a positive
    control: in **Pages** (not Notes - Notes prints links as plain text),
    type a web address so it becomes a link, export as PDF, save it to
    Files, then open it from **Files** and tap the link there: Safari must
    open. If it does not, the PDF has no live link and proves nothing -
    make another. Then attach that PDF to a site log entry, save, open it
    with View, and:
    - **tap** the link: the drawing stays exactly where it is - no Safari,
      no new page inside the viewer, no prompt; Close still works;
    - **long-press** the link: no page preview may appear and Safari must
      not open. A plain sheet offering Open / Copy / Share may appear -
      that is WebKit's own and is accepted; if you choose **Open** on it,
      nothing may happen either.
16. **A recording the phone cannot decode.** Preparation: save any
    screenshot to Files ("On My iPhone"), rename it there to `broken.m4a`
    and confirm "Use .m4a". Then in ONE new site log entry: **record a real
    voice note**, and attach `broken.m4a` as a document (its type declares
    it audio; the server trusts the declared type - if Play is not offered
    on it after saving, this scenario is no longer producible this way and
    that is a note, not a defect). Save, open the record:
    - Play on `broken.m4a`: PASS = the failure text appears within about
      20 seconds and Share is still offered. FAIL = "Loading…" beyond
      about 20 seconds, or nothing at all.
    - then Play the real voice note in the same record. Its tap clears the
      broken row's message (expected). PASS = it plays, OR it shows the
      failure text within about 20 seconds (Apple's player, once failed,
      may stay failed for the visit - a known, accepted limit). FAIL =
      "Loading…" beyond about 20 seconds.
    - go back to the list and reopen the record: the real voice note MUST
      now play. If it does not, that is a defect.
