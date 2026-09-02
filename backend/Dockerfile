# Build context is the repo root (pyproject.toml/uv.lock live there, not
# under backend/) — see docker-compose.yml's `context: .` for this service.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependencies first, separate from app code, so editing backend/ doesn't
# invalidate this (much slower) layer on every rebuild.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY backend/ ./backend/

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app/backend

EXPOSE 8020

RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]
