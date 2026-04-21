"""FastAPI application factory and module-level ``app`` instance."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    ``/healthz`` is defined inline here (not through ``app.api.router``) so
    that the health check works before Task 5 introduces the API router tree.
    When Task 5 lands, it will add ``app.include_router(api_router)`` below;
    ``/healthz`` should stay here.
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

    return app


app = create_app()
