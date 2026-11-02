# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install deps first (cached layer), then the app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev --extra llm --extra infra

COPY data ./data

EXPOSE 8000
# Non-root for safety
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["uv", "run", "uvicorn", "speed_to_lead.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
