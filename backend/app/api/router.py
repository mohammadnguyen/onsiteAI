"""Top-level API router that composes the per-resource routers.

Mounted at the root of the FastAPI app from :func:`app.main.create_app`.
The plan's URLs are unprefixed (``/auth/login``, not ``/api/v1/auth/login``)
so this router adds no prefix of its own.
"""

from fastapi import APIRouter

from app.api import auth, categories, jobs

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(categories.router, prefix="/categories")
api_router.include_router(jobs.router, prefix="/jobs")
