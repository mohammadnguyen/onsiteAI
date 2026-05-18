# Environment configuration and secrets

Operational handbook for the env/secrets layer added in Prod-readiness
Slice 1. The "what" and "why" live in
[ADR 0002](../adr/0002-environment-and-secrets-strategy.md); this
document covers the "how".

## TL;DR for a fresh clone

```bash
# Backend
cp backend/.env.development.example backend/.env.development
# (edit values if needed — defaults are fine for local dev)
cd backend && uv sync && uv run alembic upgrade head

# Admin
cp admin/.env.development.example admin/.env.development
cd admin && npm install

# Mobile
cp mobile/.env.example mobile/.env
# (edit EXPO_PUBLIC_API_URL if your backend is on a non-default host)
```

Concrete `.env*` files are gitignored. Only `.example` files are
committed. Nothing in the repo "just works" without the copy step —
that is intentional, so a fresh clone never picks up a stale or
accidentally-committed secret.

## APP_ENV is canonical

The backend selects its environment via a single env var:

| Value | Meaning |
|---|---|
| `development` | local dev; placeholder secrets and wildcard CORS permitted |
| `test`        | pytest target; non-dev gates active |
| `staging`     | non-prod hosted environment; non-dev gates active |
| `production`  | live deployment; non-dev gates active |

`APP_ENV` must be the only environment selector in active use.
`ENVIRONMENT` is honoured for one transitional release with the
following behaviour:

| `APP_ENV` set? | `ENVIRONMENT` set? | Result |
|---|---|---|
| yes | no | uses `APP_ENV` |
| yes | yes, **same** value | uses `APP_ENV`; emits `DeprecationWarning` |
| yes | yes, **different** value | **fails fast** with a clear error |
| no  | yes | uses `ENVIRONMENT`; emits `DeprecationWarning` |
| no  | no | defaults to `development` |

Future removal of `ENVIRONMENT` is its own slice; do not write new
code that sets `ENVIRONMENT`.

## Variable inventory (backend)

The loader looks for `backend/.env.{APP_ENV}` in the current working
directory. If the per-environment file is missing AND `APP_ENV=development`,
it falls back to a legacy `backend/.env`. The legacy fallback is **not**
used for `test`, `staging`, or `production` — picking up a developer's
local dev file in a non-dev process is precisely the silent-mixing
failure mode this loader is designed to prevent. In non-dev environments
without the per-env file, settings come from the process environment
alone.

| Var | Required | Where it's read | Notes |
|---|---|---|---|
| `APP_ENV` | no (defaults to `development`) | `app.config._resolve_app_env` | Lowercased + stripped at read time. |
| `DATABASE_URL` | yes | `app.config.Settings`, `app.database` | Use the `postgresql+asyncpg://` scheme. |
| `JWT_SECRET` | yes | `app.config.Settings`, `app.core.security` | Validated: ≥32 chars + not a placeholder, in any non-dev env. |
| `JWT_ALGORITHM` | no (default `HS256`) | `app.core.security` | Change requires re-evaluating refresh-token rotation. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no (default `60`) | `app.core.security` | |
| `REFRESH_TOKEN_EXPIRE_DAYS` | no (default `30`) | `app.core.security` | |
| `CORS_ALLOWED_ORIGINS` | yes (non-dev) | `app.config.Settings`, `app.main` | Comma-separated. See format spec below. |
| `ENVIRONMENT` | no (deprecated) | `app.config.Settings` | Only for legacy / migration; do not set in new files. |

### Admin

| Var | Read by |
|---|---|
| `VITE_API_URL` | Vite, baked into the JS bundle at build time. |

### Mobile

| Var | Read by |
|---|---|
| `EXPO_PUBLIC_API_URL` | `mobile/app.config.ts`; baked into the JS bundle. |
| `REACT_NATIVE_PACKAGER_HOSTNAME` | Metro bundler; needed when running on a physical device over LAN. |

**Never put a secret in any `VITE_*` or `EXPO_PUBLIC_*` variable.** Both
prefixes bake the value into the shipped client bundle.

## CORS_ALLOWED_ORIGINS format

Wire format: comma-separated origins.

```
CORS_ALLOWED_ORIGINS=https://admin.example.com,https://app.example.com
```

Rules:

- Each origin is `.strip()`-ed.
- Empty entries (after strip) are dropped.
- Each origin must include the scheme (`https://`).
- No trailing slash (`https://admin.example.com/` ≠ `https://admin.example.com`
  to a browser's CORS check — the trailing slash is a frequent prod outage).
- In any non-development environment:
  - the resolved list must be **non-empty**,
  - the wildcard `"*"` is **rejected** (must list explicit origins),
  - blank-only entries trigger no extra check (they were already dropped).
- In development the list may be empty or contain `"*"`.

## Fail-fast checks (non-development only)

When `APP_ENV ∈ {test, staging, production}`, `app.config.Settings`
refuses to construct if any of these is true:

- `JWT_SECRET` value (case-insensitive, stripped) is in the placeholder
  set `{"", "change-me", "change-me-in-prod", "changeme", "placeholder", "secret"}`.
- `len(JWT_SECRET.strip()) < 32`.
- `CORS_ALLOWED_ORIGINS` parses to an empty list.
- `CORS_ALLOWED_ORIGINS` contains a `"*"` entry.
- `APP_ENV` and `ENVIRONMENT` resolve to different values.

The error message names the offending field and the resolved `APP_ENV`.

## Startup log

The backend emits one structured log line at app-factory time
(`app.main.create_app`):

```
settings_loaded app_env=development env_file_loaded=.env.development \
  jwt_secret_present=true jwt_secret_valid=false cors_origin_count=1
```

`jwt_secret_valid` reflects "would pass the non-dev validator" — so in
development with the placeholder secret it reports `false`, which is
fine. The log line MUST NOT contain the secret value or any
value-derived fingerprint (hash, prefix, length-as-int). If you ever
catch a log line that includes a portion of the secret, treat it as a
data-integrity incident: rotate `JWT_SECRET` immediately.

## Rotating JWT_SECRET

1. Generate a new value: `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
2. Update the target environment's `.env.<env>` on the host.
3. Restart the backend process. All existing access tokens become
   invalid (they were signed with the previous secret); admins +
   contributors are forced to log in again. Refresh tokens are also
   invalidated.
4. Communicate the rotation window to active users (a few minutes is
   usually fine for this product's scale).
5. Record the rotation in your ops log: date, environment, operator.

There is no key-id field on the JWT today, so dual-signing (graceful
rotation) is not supported. The "hard cut, force re-login" model is
acceptable while the product is single-tenant and small. Multi-key
rotation is a future ADR if/when needed.

## What is safe to commit

| File | Commit? | Why |
|---|---|---|
| `*.env.example` | yes | Templates only; no real values. |
| `*.env.development.example` | yes | Dev defaults are intentional; placeholder secret is gated by validator. |
| `*.env.{test,staging,production}.example` | yes | Pure templates with `<REPLACE>` markers. |
| `*.env`, `*.env.development`, `*.env.test`, `*.env.staging`, `*.env.production`, `*.env.local` | **no** | Gitignored by `.env.*` rule. |
| `docker-compose.yml` | yes | Uses `env_file:` reference; no literal secret. |

If you ever accidentally commit a concrete `.env` file with real
secrets, rotate every secret it contained before doing anything else;
removing the file from history alone is not a fix.

## Deployment promotion walkthrough

When a real staging or production host is provisioned (separate
slice), the path from dev change to deployed config is:

1. Edit `backend/.env.production.example` (commit) — schema change
   only, no secret values.
2. On the production host, edit `backend/.env.production` (not
   committed) to match the new schema; supply real values for any
   added vars.
3. Restart the backend process. The fail-fast validator catches any
   missing required var with a clear error.
4. Verify the startup log line matches expectations (correct
   `app_env`, expected `cors_origin_count`, `jwt_secret_valid=true`).
5. Smoke-test one authenticated endpoint from the admin in a browser
   (CORS origin actually reachable).

The same flow applies to staging; the only difference is which
`.env.<env>` file you edit and which host you restart.

## Hosting-provider secret injection (Fly.io)

Per [ADR 0003](../adr/0003-staging-deployment-strategy.md), Fly.io is
the chosen staging hosting provider. Secret injection on Fly uses
`flyctl secrets set`, never `.env.*` files on the Fly host. Concrete
`.env.staging` / `.env.production` files NEVER ship to Fly; they exist
only for local-dev simulation of those environments.

The full runbook with operator approval gates lives in
[`docs/operations/staging-deploy.md`](./staging-deploy.md) (Gates D-4
and D-5). The summary below is the secret-handling subset.

Setting all required staging secrets in one command:

```
flyctl secrets set \
  APP_ENV=staging \
  JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  CORS_ALLOWED_ORIGINS="https://<staging-admin-host>" \
  --app sitetracker-backend-staging
```

Listing secrets (values are NEVER printed — only names):

```
flyctl secrets list --app sitetracker-backend-staging
```

Rotating a secret (atomic; the app restarts with the new value):

```
flyctl secrets set JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  --app sitetracker-backend-staging
```

Removing a secret:

```
flyctl secrets unset SOME_SECRET --app sitetracker-backend-staging
```

`DATABASE_URL` is auto-injected by `flyctl postgres attach` and must
NOT be set manually:

```
flyctl postgres attach sitetracker-pg-staging --app sitetracker-backend-staging
```

After this command, `DATABASE_URL` appears in `flyctl secrets list`
automatically.

Notes:

- `JWT_SECRET` is generated inline via `python -c` so the literal
  value never appears in the operator's shell history.
- The Slice 1 fail-fast validator catches placeholder / short
  `JWT_SECRET`, empty / wildcard `CORS_ALLOWED_ORIGINS`, and
  `APP_ENV` / `ENVIRONMENT` conflicts — these errors surface on the
  next deploy if secrets are misconfigured.
- The startup log emits `jwt_secret_present` and `jwt_secret_valid`
  booleans only; no secret value, hash, or prefix appears anywhere.
- DO NOT paste secret values into chat, commit messages, or
  documentation.

## Out of scope for Slice 1

Each of the following is a separate future slice with its own plan
and (where applicable) ADR:

- HTTPS / TLS termination and reverse-proxy choice.
- Hosting provider selection.
- A real secrets manager (Vault, AWS Secrets Manager, 1Password
  Connect, Doppler, …) — when introduced, it will supersede the
  `.env.*` loader described here.
- Backup and restore scripts + scheduling.
- Logging aggregation and structured log shipping.
- Monitoring (Sentry, Prometheus, OpenTelemetry, healthchecks.io).
- TestFlight / EAS build pipeline and app signing.
- CI/CD (GitHub Actions, GitLab CI, …).
- Removal of the deprecated `ENVIRONMENT` fallback (one-line slice,
  scheduled after the warning has shipped for at least one release).
