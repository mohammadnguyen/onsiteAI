# ADR-005 — Forey Test is a separate app, not a rebuilt Forey

- Status: **ACCEPTED** — founder approved the direction on 2026-09-19.
  Acceptance covers the ARCHITECTURE only. It is not authorisation to
  create cloud resources, generate signing assets, build, submit or
  publish; each of those remains a separate operator decision.
- Date prepared: 2026-09-19
- Supersedes nothing. Extends `docs/adr/0004-mobile-testflight-distribution.md`.

## Context

Forey on the founder's iPhone carries real daily business: real jobs, real
expenses, real evidence, a signed-in session, and local capture drafts that
have not been sent yet. The backend it talks to is named `staging` but is
operationally production.

The Site Log capture flow (PR #19) needs verification on a real device:
microphone and photo permissions, recording and playback, attachment
upload, offline retention across a restart, and reopening a saved record.

Reusing the same application identity and only changing the API address
would install the test build **over** Forey. That replaces the app the
founder depends on, and the install destroys nothing on the server but does
put the real app - and its unsent local drafts - at risk for the duration
of the test. Changing only a build profile does not make two apps; the
identity does.

## Decision (proposed)

Ship a second, separately installable app:

| | Forey | Forey Test |
|---|---|---|
| Display name | Forey | **Forey Test** |
| iOS bundle identifier | `com.forey.app` | **`com.forey.app.test`** |
| Android package | `com.forey.app` | `com.forey.app.test` |
| URL scheme | `forey` | **`foreytest`** |
| API | staging (real business data) | its own test API, **required explicitly** |
| Build profile | `production`, `preview` | **`test`** |
| Settings -> Diagnostics | as today | additionally shows `FOREY TEST — test data only` |

`FOREY_VARIANT=test` selects it, and the `test` build profile sets that.
The config **refuses to build** the test variant when `EXPO_PUBLIC_API_URL`
is missing, is not https, or resolves to a protected host - today that list
is the staging backend. There is no silent fallback to the real API.

## Why a different bundle identifier is the mechanism

On iOS the bundle identifier is what the sandbox is keyed to. Two apps with
different identifiers get:

- different keychains, so `expo-secure-store` tokens do not cross
  (this project sets no keychain access group, so the default per-app
  keychain applies);
- different Documents directories, so retained Site Log attachments cannot
  be seen, overwritten or deleted by the other app;
- different `AsyncStorage`, so drafts, failed captures and preferences stay
  apart;
- separate App Store Connect records, so a TestFlight build of one can
  never be delivered as an update to the other.

This project uses no App Groups, no associated domains and no
`expo-updates`, so there is no shared container, universal link or OTA
channel to separate. The one thing that WAS shared is the URL scheme;
`foreytest` separates it.

## Consequences

- A second App Store Connect app record and its own TestFlight testers are
  needed. That is an operator action, not authorised by this ADR.
- The test app needs its own backend. See
  `docs/operations/forey-test-backend.md`.
- Forey's own configuration is unchanged. Proved by running
  `npx expo config --type public` with no variant set: name `Forey`, bundle
  `com.forey.app`, scheme `forey`.

## Alternatives rejected

- **Same identity, different API URL.** Installs over Forey. Rejected by
  the founder for exactly that reason.
- **expo-dev-client / development build.** A second distribution mechanism
  to learn and maintain, when TestFlight already works for this app.
- **Android build.** Same reason; the device to be tested is an iPhone.
