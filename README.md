# MM PNL Dashboard

FastAPI backend + React (Vite) frontend tracking Trading Technologies accounts, fills, and PNL.

## Running with Docker (production)

```
cp .env.example .env   # fill in POSTGRES_PASSWORD, TT_API_KEY, TT_API_SECRET
docker compose up -d --build
```

- Backend: `http://<host>:8020`
- Frontend: `http://<host>:5190`

Postgres data lives in the `pgdata` named volume — redeploying (`docker compose up -d --build`) rebuilds the app containers and re-runs Alembic migrations, but never touches that volume. Only `docker compose down -v` removes it.

Pushing to `main` runs [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) on a self-hosted GitHub Actions runner installed on the deploy machine, which does exactly the `docker compose up -d --build` above.

GitHub Actions self-hosted runners are registered per-repository (or per-org, but that needs org admin) — a runner registered against one repo won't pick up another repo's workflow jobs. If the deploy machine already runs a different runner (as ours does, for StructureHub — see `C:\actions-runner`), install a **second, separate** runner instance registered specifically against `mm-pnl` (its own folder + its own registration token from this repo's Settings → Actions → Runners) rather than reusing the existing one. The Compose stack is named `mm-pnl` (see `docker-compose.yml`'s top-level `name:`) so it's unambiguous in `docker compose ls`/`docker ps` output next to any other project on the same box, and its ports (8020/5190) and volumes are fully isolated from whatever else is already running there.

## Running locally (no Docker)

Backend needs its own Postgres (or point `DATABASE_URL` at any Postgres) and `backend/.env` filled in from `backend/.env.example`.

```
uv sync
cd backend && alembic upgrade head && uv run python main.py   # http://localhost:8021
cd frontend && npm install && npm run dev                      # http://localhost:5191
```

`uv run python main.py` auto-restarts on code changes (uvicorn's `reload=True`, already set in `main.py`) — no extra flag needed.

Local dev intentionally runs on **8021/5191** (+1 from the Docker/production ports **8020/5190**), so both can run on the same machine at once without colliding. The local dev frontend also shows a **DEV** badge next to the logo so it's never mistaken for the production instance.
