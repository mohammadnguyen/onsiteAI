# SiteTracker Backend

FastAPI + SQLAlchemy (async) + PostgreSQL. Managed with [uv].

See the [root README](../README.md) for the monorepo quickstart.

## Requirements

- Python 3.12 (uv will download it on first use)
- [uv] 0.11+
- PostgreSQL 16 (run via `docker compose up -d db` from the repo root)

## Setup

```bash
cd backend
cp .env.example .env          # edit values as needed
uv sync                       # installs runtime + dev deps from uv.lock
```

## Run

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/healthz — should return `{"status":"ok"}`.
OpenAPI docs at http://localhost:8000/docs.

## Test

```bash
uv run pytest -v
uv run pytest --cov=app --cov-report=term-missing
```

Async tests use `asyncio_mode = "auto"` (configured in `pyproject.toml`), so
no `@pytest.mark.asyncio` is strictly required — it's still fine to use it.

## Lint & format

```bash
uv run ruff check .
uv run ruff check --fix .
uv run black .
```

## Docker

The backend image is built by the root `docker-compose.yml`. From the repo
root:

```bash
docker compose up --build backend
```

Hot reload is enabled inside the container (uvicorn `--reload`) and the
source tree is bind-mounted, so edits on the host reload the server.

## Project layout

```
backend/
  app/
    __init__.py
    config.py        # Pydantic Settings (reads .env)
    database.py      # async engine, sessionmaker, get_db dependency
    main.py          # FastAPI app factory + /healthz
  tests/
    conftest.py      # httpx AsyncClient fixture
    test_health.py
  pyproject.toml     # uv-managed project + tool config
  uv.lock            # committed lockfile
  Dockerfile
  .env.example
```

[uv]: https://docs.astral.sh/uv/
