# 0003 — Staging Deployment Strategy

## Status

Accepted (2026-05-18).

## Context

The product needs a hosted backend reachable from a TestFlight-installed
iPhone over HTTPS for the first real-user trial. The current dev-laptop
LAN setup does not satisfy this. Constraints: single AU residential
builder customer in NSW; 1-2 person operator team; no DevOps headcount;
iOS-first product; AU latency requirements. This ADR records the
**staging-tier** deployment decision only; production promotion is a
separate later ADR.

## Decision

**Hosting target.** Fly.io.

**Region.** Sydney (`syd`).

**Production-grade DB.** Fly Managed Postgres in `syd`, attached to the
backend app via `flyctl postgres attach` (auto-injects `DATABASE_URL`
as a Fly secret).

**HTTPS.** Provider-native LetsEncrypt. The default
`<app>.fly.dev` certificate is the V1 endpoint; a custom-domain
certificate via `flyctl certs create` is a follow-up once DNS is
configured.

**Secret injection.** Provider-native `flyctl secrets set`. Slice 1's
`app.config.Settings` validator continues to enforce non-dev gates:
placeholder/short `JWT_SECRET` rejected, empty/wildcard
`CORS_ALLOWED_ORIGINS` rejected, `APP_ENV`/`ENVIRONMENT` conflict
rejected.

**Alembic migrations.** Manual for V1. After each deploy, operator
runs `flyctl ssh console` + `uv run alembic upgrade head` interactively
per `docs/operations/staging-deploy.md` Gate D-7. `release_command`
auto-run is intentionally deferred to a future slice so the first
migration's output is observable.

**Rollback.** Manual for V1. App revert via `flyctl releases rollback
<version>`; DB revert via `flyctl postgres backup restore <snapshot>`.
Operator decision gates documented in `docs/operations/rollback.md`.

**Backup strategy.** Fly Postgres automatic daily snapshots (provider-
native, in-region) + manual `pg_dump` fallback. Rehearsal restore is
mandatory before any production data is trusted; procedure in
`docs/operations/staging-backup-restore.md`. Rehearsal executes in
Slice 2-B3.

**Scope.** This ADR covers **staging only**. Production deployment is
a separate slice with its own ADR. No CI/CD automation; no Sentry /
logging aggregation; no real secrets manager; no multi-region;
no TestFlight pipeline. Each is its own future ADR / slice.

**Alternative considered — Render Singapore.** Rejected for staging
because Singapore is not Sydney-local: round-trip from Sydney clients
is ~80 ms via Render Singapore vs ~5 ms intra-region on Fly `syd`.
Render remains the named fallback IF Fly `syd` is unavailable at
implementation time (capacity-constrained or marked preview). If that
fallback fires, this ADR is amended in place to note the temporary
move; if the move becomes permanent, a successor ADR supersedes 0003.

## Consequences

- A staging environment will exist (after Slice 2-B2), ready to grow
  into production via a future slice (not this one).
- Single-region single-provider for V1; multi-region and multi-cloud
  are deferred.
- Cleartext secrets remain "on Fly's side" via the provider secret
  store; `.env.*` files NEVER ship to Fly.
- Fly Postgres is acceptable for V1 single-customer scale; if the
  trial grows to multi-tenant or >100 GB DB, an external managed
  Postgres (Neon, Supabase, RDS, …) could supersede via a new ADR.
- Manual alembic + manual rollback for V1 = operator visibility on
  every state change; automation is its own future slice.
- A backup-restore rehearsal completes in Slice 2-B3 before any
  production data is committed to staging.
- Apple Developer Program enrollment, TestFlight build pipeline,
  CI/CD automation, Sentry / logging aggregation, mobile offline
  queue, real LLM — all NOT in this ADR; each is its own future
  ADR / slice.
- Custom domain DNS, monitoring dashboards — explicitly deferred.
- The Fly secret-injection procedure is documented in
  `docs/operations/env-and-secrets.md`; the deploy / backup-restore /
  rollback procedures live in dedicated docs under
  `docs/operations/`.
