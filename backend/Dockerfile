FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (cached layer) based on the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Then copy source. Rebuilds skip the dep-install layer on code-only changes.
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
