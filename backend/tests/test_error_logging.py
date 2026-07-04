"""Tests for the M0 unhandled-exception logging handler (``app.errors``).

The handler in :func:`app.main.create_app` logs method + URL path +
exception type (with traceback) for any exception that would surface as
a 500, then returns Starlette's default plain-text 500 body. Starlette's
``ServerErrorMiddleware`` re-raises the original exception after the
response is built, which is why the HTTP calls below are wrapped in
``pytest.raises`` — httpx's ``ASGITransport`` surfaces the re-raised
exception to the caller (a real server sends the 500 and logs).

These tests deliberately do NOT use the DB-backed ``client`` fixture
from ``conftest.py``: the routes under test never touch the database,
and a standalone client keeps the logging contract testable without the
dockerised test DB running.
"""

from __future__ import annotations

import logging

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

# Test-only routes that raise. Registered once at import time (module
# scope) so repeated fixture use doesn't duplicate them. The ``/_test``
# prefix keeps them visually distinct from real API surface.


@app.get("/_test/boom", include_in_schema=False)
async def _boom():  # pragma: no cover - raises before returning
    raise RuntimeError("boom")


@app.post("/_test/boom-post", include_in_schema=False)
async def _boom_post():  # pragma: no cover - raises before returning
    raise RuntimeError("boom-post")


@pytest_asyncio.fixture
async def raw_client():
    """ASGI client without the DB dependency override (no DB needed here)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.mark.asyncio
async def test_unhandled_exception_logs_method_path_and_type(raw_client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"), pytest.raises(RuntimeError):
        await raw_client.get("/_test/boom?reason=SENSITIVE_QUERY_VALUE")

    records = [r for r in caplog.records if r.name == "app.errors"]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "method=GET" in msg
    assert "path=/_test/boom" in msg
    assert "exc_type=RuntimeError" in msg
    # Traceback attached — real production-debugging value.
    assert records[0].exc_info is not None
    # Privacy: the query string must never appear in the log output.
    assert "SENSITIVE_QUERY_VALUE" not in caplog.text


@pytest.mark.asyncio
async def test_request_body_never_logged(raw_client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"), pytest.raises(RuntimeError):
        await raw_client.post(
            "/_test/boom-post", json={"note": "SENSITIVE_BODY_VALUE"}
        )

    assert any(r.name == "app.errors" for r in caplog.records)
    # Privacy: request bodies must never appear in the log output.
    assert "SENSITIVE_BODY_VALUE" not in caplog.text


@pytest.mark.asyncio
async def test_healthy_route_produces_no_error_log(raw_client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        resp = await raw_client.get("/healthz")

    assert resp.status_code == 200
    assert not [r for r in caplog.records if r.name == "app.errors"]
