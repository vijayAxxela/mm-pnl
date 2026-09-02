# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database.db import init_db, get_db, SessionLocal
from routes import accounts, products, pnl, fills, ui_state
from routes.TT_routes import TTClient, IST, TRADING_DAY_START_HOUR, TRADING_DAY_START_MINUTE
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

# "Day Open PNL" snapshot fires 15 minutes after each trading day starts —
# giving the 6:30am fill-sync window (see routes.fills._trading_day_window_ns)
# a head start to land that morning's fills before the snapshot is taken.
DAY_OPEN_SNAPSHOT_MINUTES_AFTER_START = 15

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        raise Exception("config.json not found. Please create it in the root directory.")
    except json.JSONDecodeError:
        raise Exception("config.json is not valid JSON.")

config = load_config()

tt_client = TTClient(
    api_key=config['tt_api']['api_key'],
    api_secret=config['tt_api']['api_secret'],
    environment=config['tt_api']['environment']
)

def _run_scheduled_fill_sync():
    """Runs in a worker thread (see _fill_sync_loop) — sync_all_accounts_fills
    is synchronous (requests + a sync SQLAlchemy session), so it must not run
    directly on the event loop."""
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
    except Exception as e:
        logger.error(f"Scheduled fill sync failed: {e}")
    finally:
        db.close()


async def _fill_sync_loop():
    while True:
        await asyncio.to_thread(_run_scheduled_fill_sync)
        await asyncio.sleep(FILL_SYNC_INTERVAL_SECONDS)


def _run_day_open_snapshot():
    """Runs in a worker thread (see _daily_snapshot_loop) — snapshot_day_open_pnl
    is synchronous, same reasoning as _run_scheduled_fill_sync above."""
    db = SessionLocal()
    try:
        result = pnl.snapshot_day_open_pnl(db, tt_client)
        logger.info(
            f"Day-open PNL snapshot for {result['snapshot_date']}: "
            f"{result['contracts_snapshotted']} contract(s)"
        )
    except Exception as e:
        logger.error(f"Day-open PNL snapshot failed: {e}")
    finally:
        db.close()


async def _daily_snapshot_loop():
    """
    Sleeps until the next 6:30am-IST-plus-15min mark (see
    DAY_OPEN_SNAPSHOT_MINUTES_AFTER_START), takes the snapshot, then repeats
    for the following day — rather than polling, since this only needs to
    fire once per trading day.
    """
    while True:
        now_ist = datetime.now(IST)
        today_snapshot_time = now_ist.replace(
            hour=TRADING_DAY_START_HOUR, minute=TRADING_DAY_START_MINUTE, second=0, microsecond=0
        ) + timedelta(minutes=DAY_OPEN_SNAPSHOT_MINUTES_AFTER_START)

        next_run = today_snapshot_time if now_ist < today_snapshot_time else today_snapshot_time + timedelta(days=1)
        sleep_seconds = (next_run - now_ist).total_seconds()

        logger.info(f"Next day-open PNL snapshot at {next_run.strftime('%Y-%m-%d %H:%M:%S IST')}")
        await asyncio.sleep(sleep_seconds)
        await asyncio.to_thread(_run_day_open_snapshot)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting TT PNL Dashboard API...")

    init_db()
    logger.info("Database initialized")

    sync_task = None
    snapshot_task = None

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
            logger.info(f"Background fill sync scheduled every {FILL_SYNC_INTERVAL_SECONDS // 60} minutes")

            snapshot_task = asyncio.create_task(_daily_snapshot_loop())
            logger.info("Day-open PNL snapshot scheduler started")
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

    if snapshot_task:
        snapshot_task.cancel()
        try:
            await snapshot_task
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
        "environment": config['tt_api']['environment']
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config['server']['host'],
        port=config['server']['port'],
        reload=True
    )

