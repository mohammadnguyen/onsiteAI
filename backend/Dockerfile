FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (cached layer) based on the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then copy source. Rebuilds skip the dep-install layer on code-only changes.
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# R33: run as an unprivileged user, not root. Create the user AFTER the build
# steps (uv needs to write /app/.venv as root), then hand /app to it. Because
# appuser owns /app it can read the source and execute /app/.venv/bin/* (the
# HEALTHCHECK's python and the CMD's uvicorn), while owning nothing outside /app.
RUN useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# This image is built --no-dev for prod size; pytest/ruff/black are NOT
# available inside the container. Run tests on the host via `uv run pytest`.
# The dev compose flow uses bind-mounted source + --reload; the built image is
# only used when compose rebuilds the service from scratch.

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys;urllib.request.urlopen('http://localhost:8000/healthz',timeout=2).read();sys.exit(0)" || exit 1

# R4: the image is only reachable via Fly's edge proxy, so trust X-Forwarded-*
# from all upstream hops — otherwise every request's client IP is the proxy's.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
