#!/bin/sh
# Runs on every container start. Migrations are safe to re-run — Alembic
# only applies revisions the database hasn't seen yet, so an already-current
# database (the normal case on every redeploy after the first) is a no-op
# here. Existing data is never touched by this — schema migrations, not
# data resets.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting server on port ${PORT:-8020}..."
exec uvicorn main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8020}"
