# 0002 — Environment and Secrets Strategy

## Status

Accepted (2026-05-17).

## Context

Until this ADR, every deployment surface shared a single `.env` file
that committed a literal `JWT_SECRET=change-me-in-prod` placeholder.
`docker-compose.yml` carried the same placeholder inline. The
backend's `Settings` had an `environment` field but used it only to
toggle CORS between `["*"]` (development) and `[]` (everything else —
which blocks every cross-origin request and is unusable in any real
deployment). There was no fail-fast guard against starting the process
with the placeholder secret in a non-development environment. The
product is iOS-first and operational; a real hosted deployment is now
plausible within weeks, and shipping with the existing config layer
would be irresponsible.

This ADR covers the first of several prod-readiness slices. HTTPS,
hosting choice, backups, logging, monitoring, TestFlight, CI/CD, and
secrets-manager integration are each separate future slices.

## Decision

**`APP_ENV` is the single canonical environment selector** for the
backend. Accepted values: `development`, `test`, `staging`,
`production`. The legacy `ENVIRONMENT` env var is honoured for one
transitional release with deprecation warnings; a value mismatch
between `APP_ENV` and `ENVIRONMENT` fails fast at startup. Reading
`settings.environment` returns `settings.app_env` for backward
compatibility.

**Per-environment `.env.{APP_ENV}` files** sit alongside a generic
`.env` fallback. Each environment ships a committed `.example`
template; concrete files are gitignored. The loader (`app.config`)
picks `.env.<APP_ENV>` if present, then falls back to `.env` **only in
development**, then to process-env-only. The legacy fallback is
restricted to development on purpose: picking up a developer's local
`.env` in a non-dev process would silently mix environments, which is
the precise failure mode this slice exists to prevent.

**`CORS_ALLOWED_ORIGINS`** is a comma-separated list parsed with
per-entry strip and blank-drop. In any non-development environment the
list must be non-empty and must not contain `"*"`. In development both
empty and `"*"` are permitted (preserves current dev behaviour).

**Fail-fast validator** (active in `test`, `staging`, `production`):
refuses to construct `Settings` if `JWT_SECRET` is a placeholder, is
shorter than 32 characters, if `CORS_ALLOWED_ORIGINS` is empty, or if
any origin is the literal `"*"`. `docker-compose.yml` no longer
carries any literal secret; the backend service loads `env_file:
backend/.env.development`, which is gitignored and created via a
documented copy step.

**Startup log** emits exactly: `app_env`, `env_file_loaded`,
`jwt_secret_present` (bool), `jwt_secret_valid` (bool),
`cors_origin_count` (int). No secret value, hash, prefix, or
value-derived fingerprint is ever logged.

**No secrets manager** is introduced in this slice. Secrets live as
plain text in `.env.<APP_ENV>` files on disk, with file permissions
the operator's responsibility. A future ADR may supersede this one
with a Vault / cloud-secrets-manager integration once a hosting
target is chosen.

## Consequences

- A fresh clone does not "just work" without the documented copy
  step (`cp backend/.env.development.example backend/.env.development`,
  same for `admin/` and `mobile/`). This is intentional: a missing
  file is a clearer failure mode than a silently-committed secret.
- `test` is treated as non-development, so `backend/tests/conftest.py`
  must pre-seed a non-placeholder `JWT_SECRET` of ≥32 chars and a
  non-empty `CORS_ALLOWED_ORIGINS` value. The full pytest suite still
  passes; tests that probe the validator monkeypatch around it.
- The admin's CORS misconfiguration (e.g. trailing slash, http-vs-https
  mismatch) becomes an operator responsibility instead of a binary
  dev/non-dev toggle. The validator catches obvious shape mistakes
  (empty, wildcard) but cannot verify URL correctness.
- `mobile/src/api/types.ts` is unaffected (no wire-shape change).
- The `ENVIRONMENT` deprecation period lasts until the next prod-
  readiness slice removes the fallback. While both vars are honoured,
  the conflict guard prevents silent divergence.
- Cleartext secrets remain on disk in `.env.<APP_ENV>` files; rotation
  requires touching the host and restarting the process. There is no
  graceful dual-signing for JWTs in this slice — rotation forces all
  active users to re-authenticate. Acceptable for the single-tenant
  builder/admin/accountant scale today; revisit if multi-tenant.
- When a hosting target and secrets manager are chosen, a successor
  ADR can supersede this one without rewriting application code (the
  `Settings` loader is the only surface that needs swapping).
- `docs/operations/env-and-secrets.md` is the canonical operator
  reference for the var inventory, validator behaviour, rotation
  procedure, and deployment promotion walkthrough.
