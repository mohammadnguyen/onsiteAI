"""API-layer (HTTP route) modules.

Each resource (``auth``, and later ``jobs``, ``categories``, ``items``)
lives in its own module and exposes an ``APIRouter`` named ``router``.
:mod:`app.api.router` composes them into the single ``api_router``
mounted on the FastAPI app.
"""
