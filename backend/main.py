# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database.db import init_db, get_db, SessionLocal
from routes import accounts, products, pnl, fills, ui_state, ws, alerts
from routes.TT_routes import TTClient, IST
from datetime import datetime, timedelta
import asyncio
import json
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# How often the background job re-syncs every account's fills for the
# current (IST) trading day, keeping the Fills table — and PNL, which reads
# straight from it — up to date without anyone touching the Fills page.
FILL_SYNC_INTERVAL_SECONDS = 20 * 60

# "Day Open PNL" snapshot fires this many minutes after each trading day
# starts (see routes.TT_routes.TRADING_DAY_START_HOUR/MINUTE, env-configurable
# via TRADING_DAY_START_TIME) — giving the fill-sync window (see
# routes.fills._trading_day_window_ns) a head start to land that morning's
# fills before the snapshot is taken.
DAY_OPEN_SNAPSHOT_MINUTES_AFTER_START = 15

def load_config():
    """
    config.json holds local-dev defaults/fallbacks only. Anything secret
    (TT credentials) or environment-specific (port) is read from the
    environment first — see the TT_API_*/PORT env vars below — so Docker
    (and CI) never need a config.json containing real credentials baked
    into the image or checked into git.
    """
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        raise Exception("config.json is not valid JSON.")

config = load_config()

tt_client = TTClient(
    api_key=os.getenv('TT_API_KEY', config.get('tt_api', {}).get('api_key')),
    api_secret=os.getenv('TT_API_SECRET', config.get('tt_api', {}).get('api_secret')),
    environment=os.getenv('TT_ENVIRONMENT', config.get('tt_api', {}).get('environment', 'ext_prod_live'))
)

def _maybe_run_day_open_snapshot(db):
    """
    Take today's Day Open PNL snapshot if it's due and hasn't been taken yet
    — called from _run_scheduled_fill_sync, AFTER that cycle's fill sync has
    committed, on the very same db session. That ordering is the point: it
    used to be a separate timer loop (_daily_snapshot_loop) racing the fill
    sync purely on wall-clock time, which could snapshot before that
    morning's fills had actually landed (or, at the exact boundary, fire
    twice back to back). Reading from a session that just committed the
    fill sync guarantees the snapshot always sees that cycle's fills.
    """
    from routes.fills import _trading_day_window_ns
    from database.db import DailyPnlSnapshot

    _, _, window_start, now_ist = _trading_day_window_ns()
    if now_ist < window_start + timedelta(minutes=DAY_OPEN_SNAPSHOT_MINUTES_AFTER_START):
        return  # too early in the trading day — give fills a head start

    snapshot_date = window_start.strftime('%Y-%m-%d')
    already_taken = db.query(DailyPnlSnapshot).filter(
        DailyPnlSnapshot.snapshot_date == snapshot_date
    ).first() is not None
    if already_taken:
        return

    result = pnl.snapshot_day_open_pnl(db, tt_client)
    logger.info(
        f"Day-open PNL snapshot for {result['snapshot_date']}: "
        f"{result['contracts_snapshotted']} contract(s)"
    )


def _run_scheduled_fill_sync():
    """Runs in a worker thread (see _fill_sync_loop) — sync_all_accounts_fills
    is synchronous (requests + a sync SQLAlchemy session), so it must not run
    directly on the event loop. Returns (last_synced_at, alert_result):
    last_synced_at is None on failure; alert_result is whatever
    alerts.check_and_fire_loss_alerts returned (None if nothing fired, or
    the sync itself failed so there's nothing new to check)."""
    db = SessionLocal()
    try:
        result = fills.sync_all_accounts_fills(db, tt_client)
        app.state.last_synced_at = datetime.now(IST)
        logger.info(
            f"Scheduled fill sync: {result['total_fills_saved']} new fill(s) "
            f"across {result['accounts_processed']} account(s)"
        )
        for r in result["results"]:
            if r["status"] == "error":
                logger.warning(f"  {r['account_name']}: {r['detail']}")

        # Fills for this cycle are committed above — safe to snapshot now if
        # today's Day Open PNL is due (see _maybe_run_day_open_snapshot).
        _maybe_run_day_open_snapshot(db)

        # Runs synchronously right here — after every fill sync, once a
        # Day Open snapshot exists for today (see the function's own
        # docstring for why it no-ops before that). Any sound alert to
        # push gets bubbled back up for _fill_sync_loop to broadcast on
        # the actual event loop; the email (if any) is already sent by
        # the time this returns.
        alert_result = alerts.check_and_fire_loss_alerts(db, tt_client)

        return app.state.last_synced_at, alert_result
    except Exception as e:
        logger.error(f"Scheduled fill sync failed: {e}")
        return None, None
    finally:
        db.close()


async def _fill_sync_loop():
    while True:
        last_synced_at, alert_result = await asyncio.to_thread(_run_scheduled_fill_sync)
        if last_synced_at is not None:
            # Push to every connected client — this is what lets the PNL
            # page (and anything else listening) update itself the moment
            # fills actually change, instead of only on a manual reload or
            # a fixed poll interval.
            await ws.broadcast({"type": "fills_synced", "last_synced_at": last_synced_at.isoformat()})
        if alert_result and alert_result.get("sound_alert"):
            await ws.broadcast(alert_result["sound_alert"])
        await asyncio.sleep(FILL_SYNC_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting TT PNL Dashboard API...")

    init_db()
    logger.info("Database initialized")

    sync_task = None

    try:

        db = next(get_db())

        logger.info("Logging into TT API...")
        login_result = tt_client.login(db_session=db)

        if login_result['status'] == 'success':
            logger.info(f" TT Login successful")
            logger.info(f" Found {login_result['account_count']} accounts")

            if login_result.get('markets_sync'):
                markets_info = login_result['markets_sync']
                logger.info(f" Markets synced: {markets_info['synced']} new, {markets_info['skipped']} existing, {markets_info['total']} total")

            logger.info("API ready to accept requests")

            sync_task = asyncio.create_task(_fill_sync_loop())
            logger.info(
                f"Background fill sync scheduled every {FILL_SYNC_INTERVAL_SECONDS // 60} minutes "
                f"(day-open PNL snapshot runs inline once {DAY_OPEN_SNAPSHOT_MINUTES_AFTER_START}min "
                "into the trading day)"
            )
        else:
            logger.error(f"✗ TT Login failed: {login_result.get('message', 'Unknown error')}")
            logger.warning("API will start but TT integration may not work properly")

        db.close()

    except Exception as e:
        logger.error(f"✗ Error during startup: {str(e)}")
        logger.warning("API will start but TT integration may not work properly")

    yield

    logger.info("Shutting down TT PNL Dashboard API...")

    if sync_task:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Trading PNL Dashboard API", lifespan=lifespan)
app.state.last_synced_at = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router, prefix="/api/accounts", tags=["Accounts"])
app.include_router(products.router, prefix="/api/products", tags=["Products"])
app.include_router(pnl.router, prefix="/api/pnl", tags=["PNL"])
app.include_router(fills.router, prefix="/api/fills", tags=["Fills"])
app.include_router(ui_state.router, prefix="/api/ui-state", tags=["UI State"])
app.include_router(ws.router, prefix="/ws", tags=["WebSocket"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])

@app.get("/")
async def root():
    return {
        "message": "TT PNL API", 
        "status": "running",
        "tt_connected": tt_client.bearer_token is not None,
        "accounts_loaded": len(tt_client.accounts)
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "tt_authenticated": tt_client.bearer_token is not None,
        "accounts_count": len(tt_client.accounts),
        "environment": tt_client.environment
    }

if __name__ == "__main__":
    import uvicorn
    # PORT env var overrides config.json — Docker/production always sets it
    # explicitly (or relies on config.json's 8020 default); local dev sets
    # PORT=8021 in backend/.env (see .env.example) so it never collides with
    # a docker-composed instance running on the same machine.
    server_config = config.get('server', {})
    uvicorn.run(
        "main:app",
        host=os.getenv('HOST', server_config.get('host', '0.0.0.0')),
        port=int(os.getenv('PORT', server_config.get('port', 8020))),
        reload=True
    )

