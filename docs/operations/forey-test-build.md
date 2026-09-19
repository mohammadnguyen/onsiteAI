# Forey Test — build and install alongside Forey (iOS)

Operator runbook. Companion to `docs/operations/testflight-build.md`, which
this does not replace: the gates, the credential rules and the
operator-only steps there all still apply.

Decision of record: `docs/decisions/ADR-005-forey-test-app-variant.md`
(PROPOSED).

## What this produces

A second app on the same iPhone, `Forey Test`, that cannot see or disturb
Forey's data and talks only to a test backend. Forey stays installed,
signed in, and untouched.

## Authoring principles

Same as the TestFlight runbook: commands are **dated illustrative examples
(2026-09-19), not canonical truth**; re-run `<command> --help` before any
state-changing step; every Apple/Expo credential is handled by the operator
and never by the assistant.

## Preconditions (operator confirms; nothing below runs until all hold)

- [ ] A test backend exists at an https address, and it is **not** the
      staging host. Preparation: `docs/operations/forey-test-backend.md`.
- [ ] Apple Developer Program enrollment is active (already true - team
      `W58T3X33VM`).
- [ ] An App Store Connect app record exists for **`com.forey.app.test`**.
      This is a new record; creating it is an operator action.
- [ ] Expo/EAS access works from the operator's machine.

## Gates

### FT-1 — Config verification (read-only, assistant may run)

Intent: prove the test variant is separate and the default is unchanged.

```
# default - must be Forey, com.forey.app, scheme forey
npx expo config --type public

# test with no API url - must REFUSE
FOREY_VARIANT=test npx expo config --type public

# test pointed at the real backend - must REFUSE
FOREY_VARIANT=test EXPO_PUBLIC_API_URL=https://sitetracker-backend-staging.fly.dev \
  npx expo config --type public

# test with its own API - must be Forey Test, com.forey.app.test, foreytest
FOREY_VARIANT=test EXPO_PUBLIC_API_URL=https://<test-api-host> \
  npx expo config --type public
```

Verify: the four outcomes above, exactly. Back-out: read-only.

### FT-2 — Test API liveness (operator or assistant, read-only)

Intent: never bake an address that does not serve this branch's API.

```
curl -sI https://<test-api-host>/healthz                 # expect 200
curl -s -o /dev/null -w "%{http_code}" \
  https://<test-api-host>/site-log-events/mine           # expect 401, NOT 404
```

`401` proves the Site Log routes are registered and protected. `404` means
the deployed backend predates them: **STOP** and deploy the right version
first. Verify both. Back-out: read-only.

### FT-3 — Build (operator; stateful, provider)

Pre: re-run `npx eas-cli build --help`. EAS will ask for Apple credentials
and will create signing assets for the **new** bundle identifier; that is
expected and is an operator decision.

```
npx eas-cli login
EXPO_PUBLIC_API_URL=https://<test-api-host> \
  npx eas-cli build --platform ios --profile test
```

Verify: the build log shows `FOREY_VARIANT=test`, the test API URL, and
bundle identifier `com.forey.app.test`. Back-out: discard the build;
nothing reaches TestFlight until FT-4.

### FT-4 — TestFlight (operator)

Submit the artefact to the `com.forey.app.test` record and install from
TestFlight. Forey is a different app record and is not updated by this.

Verify on the phone: two apps, `Forey` and `Forey Test`; Forey still opens
signed in with its own data; Forey Test opens signed out.
Back-out: delete Forey Test. Forey is unaffected.

### FT-5 — Confirm the isolation before testing anything else

- [ ] Both apps are on the home screen.
- [ ] Forey Test's Settings -> Diagnostics shows `FOREY TEST — test data
      only` and the **test** API host.
- [ ] Forey's Settings -> Diagnostics still shows the staging host.
- [ ] Signing out of Forey Test does not sign out Forey.

## Device acceptance checklist (test data only)

1. Microphone permission **denied** - the app says so and stays usable;
   then **allowed** - recording works.
2. Photo permission denied, then allowed - same.
3. Record a voice note, save the entry, reopen it, play it back.
4. Attach a photo and a document; save; reopen; open each one.
5. Turn on aeroplane mode, write an entry with an attachment, save - it
   must say the save result is unconfirmed and keep the draft.
6. Force-close the app, reopen: the text and the attachment are still
   there.
7. Restore the network, resume - exactly ONE record is created.
8. Start a capture, save it, and while it is saving go back and start
   another - the first one finishing must not replace or clear the second.
9. **Upgrade, not reinstall**: with an unsent draft waiting, install the
   next Forey Test build over the top. The draft and its attachments must
   still be there. Do not delete the app to test this - deleting removes
   the Documents directory and destroys the very thing under test.
