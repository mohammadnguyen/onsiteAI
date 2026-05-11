"""FastAPI application factory and module-level ``app`` instance."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    ``/healthz`` is defined inline here (not through ``app.api.router``) so
    the health check has no dependency on the API router tree. The
    feature routers (auth, jobs, categories, ...) are composed through
    :data:`app.api.router.api_router` and mounted at the root —
    Phase 1 URLs are unprefixed (no ``/api/v1`` yet).
    """
    settings = get_settings()

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
        allow_origins=["*"] if settings.environment == "development" else [],
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

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)

    return app


app = create_app()
