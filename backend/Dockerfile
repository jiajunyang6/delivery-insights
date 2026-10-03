# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir "uv>=0.4"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY eval ./eval
RUN uv sync --frozen --no-dev
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN useradd --create-home --uid 10001 app
COPY --from=builder /app /app
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
CMD ["uvicorn", "insights.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--log-config", "/app/src/insights/logging.json"]
