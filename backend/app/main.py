"""FastAPI application factory and module-level ``app`` instance."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.router import api_router
from app.config import get_settings, resolved_env_file_path

logger = logging.getLogger("app.startup")


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """Startup/shutdown hook.

    Audit E6: migrations are manual for V1 (ADR 0003), so a deploy can ship
    app code expecting a migration a human forgot to run. This emits a single
    non-fatal ``schema_head_check`` line comparing the packaged Alembic head
    to the DB's current head so drift is observable in logs. It NEVER blocks
    startup — any failure (DB down, table absent) degrades to a skipped log.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        from app.database import get_engine

        packaged_head = ScriptDirectory.from_config(
            Config("alembic.ini")
        ).get_current_head()
        async with get_engine().connect() as conn:
            db_head = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
        logger.info(
            "schema_head_check packaged_head=%s db_head=%s schema_head_matches=%s",
            packaged_head,
            db_head,
            packaged_head == db_head,
        )
    except Exception as exc:  # never block startup on an observability check
        logger.warning("schema_head_check_skipped error=%s", type(exc).__name__)
    yield

# M0 observability: dedicated logger for unhandled request exceptions.
# Lives beside the startup logger so log filtering can target either
# channel independently ("app.startup" vs "app.errors").
error_logger = logging.getLogger("app.errors")


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    ``/healthz`` is defined inline here (not through ``app.api.router``) so
    the health check has no dependency on the API router tree. The
    feature routers (auth, jobs, categories, ...) are composed through
    :data:`app.api.router.api_router` and mounted at the root —
    Phase 1 URLs are unprefixed (no ``/api/v1`` yet).
    """
    settings = get_settings()

    # Prod-readiness Slice 1 / ADR 0002: emit a single startup line that
    # confirms the resolved environment + booleans only. NEVER log a
    # secret value, hash, prefix, or any value-derived fingerprint.
    logger.info(
        "settings_loaded app_env=%s env_file_loaded=%s "
        "jwt_secret_present=%s jwt_secret_valid=%s cors_origin_count=%d",
        settings.app_env,
        resolved_env_file_path(),
        bool(settings.jwt_secret),
        settings.jwt_secret_is_valid,
        len(settings.cors_allowed_origins),
    )

    # Audit E5: the API is internal-only ("Proprietary - Internal Use Only");
    # expose the interactive docs / raw OpenAPI schema only in dev + test, not
    # on the public staging/production URL where they hand an attacker the full
    # route + schema map for free.
    _docs_enabled = settings.app_env in {"development", "test"}
    app = FastAPI(
        title="SiteTracker API",
        version="0.1.0",
        description="Internal cost control API for small residential builders",
        lifespan=_lifespan,
        docs_url="/docs" if _docs_enabled else None,
        redoc_url="/redoc" if _docs_enabled else None,
        openapi_url="/openapi.json" if _docs_enabled else None,
        contact={
            "name": "SiteTracker Engineering",
            "email": "engineering@sitetracker.internal",
        },
        license_info={
            "name": "Proprietary - Internal Use Only",
        },
        openapi_tags=[
            {"name": "system", "description": "Service health and readiness."},
            {"name": "auth", "description": "Login, refresh, logout, current user."},
            {"name": "users", "description": "Admin user invites, list, and deactivation."},
            {"name": "jobs", "description": "Jobs + aliases + category budgets."},
            {"name": "categories", "description": "Builder-category catalogue (23 seeded)."},
        ],
    )

    app.add_middleware(
        CORSMiddleware,
        # Prod-readiness Slice 1 / ADR 0002: the allow-origins list is now
        # config-driven (comma-separated CORS_ALLOWED_ORIGINS). The
        # non-dev validator in app.config refuses to start the process if
        # this list is empty or contains "*" outside development, so by
        # the time we get here the value is guaranteed safe for the
        # current environment.
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Phase 4: the admin's Excel-export hook reads the filename out
        # of ``Content-Disposition`` to drive the browser save dialog.
        # That header is not on the CORS "simple response headers" list
        # and so must be explicitly exposed; without this, browsers
        # strip it from cross-origin responses and the filename
        # round-trip silently degrades to the fallback "export.xlsx".
        expose_headers=["Content-Disposition"],
    )

    # M0 observability: explicit app-level logging for unhandled
    # exceptions (anything that would surface as a 500). Starlette's
    # ServerErrorMiddleware invokes this handler to build the 500
    # response and then RE-RAISES the original exception, so existing
    # server-level behaviour (uvicorn error logging, test-client
    # exception propagation) is unchanged. The response body matches
    # Starlette's default plain-text 500 exactly — wire behaviour is
    # preserved for clients.
    #
    # Privacy rules (same spirit as the startup log above): log the
    # method + URL *path* + exception type only. NEVER log query
    # strings (they can carry business text such as delete reasons),
    # request bodies, headers, tokens, or secret values. The attached
    # ``exc_info`` traceback contains code locations, not payloads.
    @app.exception_handler(Exception)
    async def _log_unhandled_exception(
        request: Request, exc: Exception
    ) -> PlainTextResponse:
        error_logger.error(
            "unhandled_exception method=%s path=%s exc_type=%s",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc_info=exc,
        )
        return PlainTextResponse("Internal Server Error", status_code=500)

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)

    return app


app = create_app()
