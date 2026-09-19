# Forey Test backend — preparation

**Nothing in this document has been created, deployed or migrated.** It is
the preparation the founder asked for: what would be needed, what it would
cost, and what is left to approve.

The existing backend (`sitetracker-backend-staging.fly.dev`) is
operationally production - it carries real business data - and is not
touched by any step here.

## What it has to be

A backend the iPhone can reach over cellular, with the development machine
switched off, serving the Site Log API this branch adds.

- **Code version:** the accepted head of PR #19 (currently
  `b6061eb969b26d758e50b0987387af24e6b369b7`). The Site Log routes are not
  on `main` and are not on staging: an unauthenticated
  `GET /site-log-events/mine` against staging returns **404** while
  `/expenses` returns 401, which is how we know the router is absent there.
- **Database:** its own Postgres, migrated to this branch's alembic head.
  It must contain the Site Log tables (`c7d8e9f0a1b2`, `d9e0f1a2b3c4`) that
  staging has never had. No copy of real data, ever - synthetic records
  only.
- **Attachment storage:** persistent, not a container disk that resets on
  deploy. Either a small object-storage bucket of its own, or the local
  adapter with a mounted volume. If the local adapter is used, that proves
  the local storage path only: **the Tigris and incomplete-multipart
  release gates stay unverified either way.**
- **Secrets:** its own `JWT_SECRET`, generated for this environment. The
  development placeholder (`change-me-in-prod`) must not be used on a
  public address, and no key from the real environment may be reused.
- **Accounts:** two synthetic users created by
  `python -m scripts.seed_admin` - one admin, one demoted to contributor -
  with passwords that exist only here.

## Resources, cost basis, lifetime

| Item | What | Cost basis |
|---|---|---|
| App host | one small always-on instance (Fly.io, same stack as staging) | Fly's shared-cpu-1x tier; a second app on the existing account. The exact figure depends on the plan attached to that account, which I have not read. |
| Database | one small Postgres, separate from staging's | Fly Postgres smallest configuration, or any managed Postgres the founder prefers |
| Attachment storage | one bucket, or a small volume | A few GB at most: the test data is a handful of photos and voice notes |
| Lifetime | for the duration of device acceptance | Delete when the acceptance run is signed off |

Cleanup is deliberately simple: the app, the database and the bucket are
created only for this, share nothing with the real environment, and are
destroyed together. Nothing needs to be migrated back.

## What is deliberately NOT decided here

- Which provider and plan. The stack is the same as staging, but the
  founder chooses whether to add a second Fly app or host it elsewhere.
- Whether to use object storage or a mounted volume for attachments.
  Either satisfies "persistent"; neither clears the Tigris gate.

## Steps, in order, when approved

1. Founder approves the resource creation and its cost.
2. Create the app, the database and the storage - operator, with the
   operator's own credentials.
3. Set `APP_ENV`, `DATABASE_URL`, a fresh `JWT_SECRET`,
   `CORS_ALLOWED_ORIGINS`, and the evidence-storage settings.
4. Deploy the accepted PR #19 head and run `alembic upgrade head`.
5. Seed the two synthetic accounts.
6. Verify from a browser or curl: `/healthz` is 200 and
   `/site-log-events/mine` is **401** (not 404).
7. Only then, build Forey Test against that address -
   `docs/operations/forey-test-build.md`.
