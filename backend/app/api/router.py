"""Top-level API router that composes the per-resource routers.

Mounted at the root of the FastAPI app from :func:`app.main.create_app`.
The plan's URLs are unprefixed (``/auth/login``, not ``/api/v1/auth/login``)
so this router adds no prefix of its own.
"""

from fastapi import APIRouter

from app.api import (
    auth,
    categories,
    evidence,
    expenses,
    jobs,
    labour,
    org_settings,
    reports,
    review_queue,
    site_log,
    suppliers,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(categories.router, prefix="/categories")
api_router.include_router(expenses.router, prefix="/expenses")
api_router.include_router(jobs.router, prefix="/jobs")
# Labour spans /workers, /labour-entries and /labour-summary (one
# feature, three roots) — included unprefixed with absolute paths.
api_router.include_router(labour.router)
# Evidence spans /evidence and /jobs/{id}/evidence — unprefixed,
# absolute paths, same pattern as labour.
api_router.include_router(evidence.router)
# Site Log spans /site-log-events and /jobs/{id}/site-log-events —
# unprefixed, absolute paths, same pattern as evidence.
api_router.include_router(site_log.router)
api_router.include_router(org_settings.router)
api_router.include_router(reports.router, prefix="/reports")
api_router.include_router(review_queue.router, prefix="/review-queue")
api_router.include_router(suppliers.router, prefix="/suppliers")
api_router.include_router(users.router, prefix="/users")
