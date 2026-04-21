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
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)

    return app


app = create_app()
