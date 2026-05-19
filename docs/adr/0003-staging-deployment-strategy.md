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

**Production-grade DB.** Fly Managed Postgres (`fly mpg`) in `syd`,
attached to the backend app via `fly mpg attach <CLUSTER_ID>` (auto-
injects `DATABASE_URL` as a Fly secret and triggers an app restart).
Plan tier is operator-selected at create time; staging baseline is
the lowest non-HA, single-node tier from Fly's MPG plan menu. Higher
tiers (HA, multi-node, performance) require a separate ADR
amendment.

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

**Rollback.** Manual for V1. App revert and DB revert procedures live
in `docs/operations/rollback.md`; both use Fly's currently-supported
tooling (release rollback for the app; MPG backup-restore for the
DB). Operator decision gates documented in that runbook.

**Backup strategy.** Fly MPG provider-managed backups (frequency and
retention per selected plan tier, in-region) + manual `pg_dump`
fallback via Fly's connect/proxy capability. Rehearsal restore is
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
- Fly MPG is acceptable for V1 single-customer scale; if the trial
  grows to multi-tenant or >100 GB DB, an external managed Postgres
  (Neon, Supabase, RDS, …) could supersede via a new ADR.
- Staging operational cost varies with the selected MPG plan tier;
  budget refresh is required when Fly changes its plan menu or
  pricing.
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

### Runbook Authoring Principles

These principles govern the operational runbooks under
`docs/operations/` that target this staging deployment (and any
future production runbook that supersedes it). They were adopted
during Slice 2-B2 after Fly deprecated two CLI surfaces mid-runbook
(`flyctl regions set` at Gate D-2; unmanaged `flyctl postgres
create` at Gate D-3 in favour of `fly mpg`).

- **Commands are examples, not canonical truth.** Each example
  command in a runbook is dated and clearly marked as illustrative.
  The runbook's authority lives in its operational intent and
  verification steps, not its CLI lines.
- **Operational intent + verification + rollback are primary.** Every
  gate describes what it accomplishes at the system level, what
  observable state must exist after it runs, and how to back out if
  the result is wrong. Intent and verification survive CLI churn;
  CLI specifics do not.
- **Provider CLIs may drift.** Fly (and any other provider chosen in
  future) reserves the right to deprecate, rename, or replace
  subcommands. Runbooks treat any CLI command as subject to change.
- **Stateful mutations require current CLI help verification.**
  Before any gate that creates, attaches, modifies, or destroys a
  provider resource, the operator re-runs the relevant `<command>
  --help` to confirm the example command in the runbook is still
  accurate. A mismatch between the runbook example and current
  `--help` blocks the gate until the runbook is amended.
- **Preserve single-gate governance language.** Each gate remains
  individually operator-approvable. The new authoring style does
  not combine, bundle, or pre-authorize multiple state-changing
  gates, and does not relax any explicit APPROVAL REQUIRED marker.
