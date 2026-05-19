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

EXPOSE 8000

# This image is built --no-dev for prod size; pytest/ruff/black are NOT
# available inside the container. Run tests on the host via `uv run pytest`.
# The dev compose flow uses bind-mounted source + --reload; the built image is
# only used when compose rebuilds the service from scratch.

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys;urllib.request.urlopen('http://localhost:8000/healthz',timeout=2).read();sys.exit(0)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
