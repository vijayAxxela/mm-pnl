"""
Manual, one-off backfill for today's Day Open PNL snapshot.

Why this exists instead of just calling snapshot_day_open_pnl(): that
function stamps day_open_pnl = cumulative realized PNL AT THE MOMENT IT
RUNS — correct only when run at the actual start of the trading day, before
any of today's fills have happened. If today's automatic snapshot (normally
fired ~15 min after TRADING_DAY_START_TIME) never ran and the day already
has real trading in it, calling that function now would wrongly stamp
"cumulative including today's trades so far" as the OPEN baseline.

This script instead FIFO-matches every fill itself (same algorithm as
routes/pnl.py's _compute_pnl_rows, duplicated here deliberately — this is a
standalone manual tool, not something get_pnl_overview/check_and_fire_loss_
alerts depend on, so it doesn't need to share code with them), but only
counts a match's PNL if its CLOSING fill happened BEFORE today. That gives
cumulative realized PNL as of today's own open specifically, regardless of
what time of day this is actually run.

Safe to re-run (upserts on account_id/instrument_id/snapshot_date, same as
snapshot_day_open_pnl). Run manually, from inside the prod backend
container so it picks up live DATABASE_URL/TT_API_KEY/TT_API_SECRET:

    docker exec mm-pnl-backend-1 python scripts/backfill_day_open_snapshot.py

Or against a locally-exposed copy of the prod DB by setting DATABASE_URL
yourself before running. Either way, TT credentials come from the normal
env (TT_API_KEY/TT_API_SECRET/TT_ENVIRONMENT) exactly like the app itself.
"""
import sys
import os
from collections import deque, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import SessionLocal, DailyPnlSnapshot, Account, Fill
from routes.pnl import _get_or_cache_instrument
from routes.fills import _trading_day_window_ns
from routes.TT_routes import TTClient, ist_date_to_ns, get_daily_usd_rate


def cumulative_realized_before_today(db, tt_client, account, today_start_str):
    """Same FIFO algorithm as _compute_pnl_rows, but only sums a match's PNL
    if its closing fill is from before today — cumulative PNL as of today's
    open. Returns {instrument_id: usd_amount}."""
    query = db.query(Fill).filter(Fill.account_id == account.id)
    if account.pnl_start_date:
        start_ns = ist_date_to_ns(account.pnl_start_date)
        query = query.filter(Fill.transact_time >= str(start_ns))
    fills = [f for f in query.all() if f.multi_leg_reporting_type != '2']
    if not fills:
        return {}

    by_instrument = defaultdict(list)
    for f in fills:
        by_instrument[f.instrument_id].append(f)

    results = {}
    for instrument_id, instrument_fills in by_instrument.items():
        try:
            product = _get_or_cache_instrument(db, tt_client, instrument_id)
        except Exception as e:
            print(f"  (skipping instrument {instrument_id}: {e})")
            continue

        tick_size = product.tickSize or 0
        tick_value = product.tickValue or 0
        sorted_fills = sorted(instrument_fills, key=lambda f: (f.transact_time, f.id))
        total_qty = sum(f.last_qty for f in sorted_fills)
        qty_epsilon = max(1e-9, total_qty * 1e-9)

        open_lots = deque()
        open_side = 0
        realized_native_before_today = 0.0

        for f in sorted_fills:
            side = 1 if f.side == 1 else -1
            qty = f.last_qty
            price = f.last_px
            is_today = f.transact_time >= today_start_str

            if open_side == 0 or side == open_side:
                open_lots.append([qty, price])
                open_side = side
                continue

            remaining = qty
            while remaining > qty_epsilon and open_lots:
                lot = open_lots[0]
                matched = min(remaining, lot[0])
                price_diff = (price - lot[1]) if open_side == 1 else (lot[1] - price)
                if tick_size and not is_today:
                    realized_native_before_today += (price_diff / tick_size) * tick_value * matched
                lot[0] -= matched
                remaining -= matched
                if lot[0] <= qty_epsilon:
                    open_lots.popleft()
            if remaining > qty_epsilon:
                open_side = side
                open_lots.append([remaining, price])
            elif not open_lots:
                open_side = 0

        usd_rate = get_daily_usd_rate(db, tt_client, product.currency_id)
        results[instrument_id] = realized_native_before_today * usd_rate

    return results


def main():
    db = SessionLocal()
    try:
        tt_client = TTClient(
            api_key=os.getenv("TT_API_KEY"),
            api_secret=os.getenv("TT_API_SECRET"),
            environment=os.getenv("TT_ENVIRONMENT"),
        )

        today_start_ns, _, window_start, _ = _trading_day_window_ns()
        today_start_str = str(today_start_ns)
        snapshot_date = window_start.strftime("%Y-%m-%d")

        accounts = db.query(Account).all()
        if not accounts:
            print("No accounts found — nothing to backfill.")
            return

        saved = 0
        for account in accounts:
            by_instrument = cumulative_realized_before_today(db, tt_client, account, today_start_str)
            for instrument_id, day_open_pnl in by_instrument.items():
                day_open_pnl = round(day_open_pnl, 2)

                existing = (
                    db.query(DailyPnlSnapshot)
                    .filter(
                        DailyPnlSnapshot.account_id == account.id,
                        DailyPnlSnapshot.instrument_id == instrument_id,
                        DailyPnlSnapshot.snapshot_date == snapshot_date,
                    )
                    .first()
                )

                if existing:
                    print(f"  {account.name} / {instrument_id}: {existing.day_open_pnl} -> {day_open_pnl}")
                    existing.day_open_pnl = day_open_pnl
                else:
                    print(f"  {account.name} / {instrument_id}: (new) {day_open_pnl}")
                    db.add(
                        DailyPnlSnapshot(
                            account_id=account.id,
                            instrument_id=instrument_id,
                            snapshot_date=snapshot_date,
                            day_open_pnl=day_open_pnl,
                        )
                    )
                saved += 1

        db.commit()
        print(f"\nBackfilled {saved} contract(s) for trading day {snapshot_date}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
