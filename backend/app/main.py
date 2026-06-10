"""FastAPI application factory and module-level ``app`` instance."""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.router import api_router
from app.config import get_settings, resolved_env_file_path

logger = logging.getLogger("app.startup")

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

    app = FastAPI(
        title="SiteTracker API",
        version="0.1.0",
        description="Internal cost control API for small residential builders",
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
