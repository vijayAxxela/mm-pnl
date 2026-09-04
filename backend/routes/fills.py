# routes/fills.py
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from database.db import get_db, Account, Fill, Position, AccountProductSettings
from datetime import datetime, timedelta
from typing import Optional
import pytz
import logging
import hashlib
import json

router = APIRouter()
logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')


@router.get("/last-synced")
def get_last_synced(request: Request):
    """When the 20-minute background fill sync last completed successfully (see main.py)."""
    last = getattr(request.app.state, "last_synced_at", None)
    return {"last_synced_at": last.isoformat() if last else None}


@router.post("/sync")
def sync_fills(
    days_back: int = 1,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    """
    Sync fills from TT API for all accounts that have product settings configured
    
    Args:
        days_back: Number of days to look back from now (default: 1)
    """
    from main import tt_client
    
    # Get all accounts that have products configured
    accounts_with_products = db.query(Account).join(AccountProductSettings).distinct().all()
    
    if not accounts_with_products:
        raise HTTPException(
            status_code=404,
            detail="No accounts with product settings found. Please configure products for accounts first."
        )
    
    # Calculate time range in IST
    now_ist = datetime.now(IST)
    start_time_ist = now_ist - timedelta(days=days_back)
    
    # Convert to UTC for TT API
    start_time_utc = start_time_ist.astimezone(pytz.UTC)
    start_time_ns = int(start_time_ist.timestamp() * 1_000_000_000)

    try:
        synced_count = 0
        skipped_count = 0
        updated_count = 0

        for account in accounts_with_products:
            # Fetch fills scoped to this account and time window only (TT-side
            # filter), instead of pulling the whole company's fill history.
            account_fills = tt_client.get_all_fills(
                start_time_ns=start_time_ns,
                accountID=account.tt_account_id
            )

            # Filter by time again client-side, since TT doesn't reliably
            # honor minTimestamp/maxTimestamp server-side.
            time_filtered_fills = []
            for fill in account_fills:
                fill_timestamp = int(fill.get('transactTime', '0'))
                fill_time_utc = datetime.fromtimestamp(fill_timestamp / 1_000_000_000, tz=pytz.UTC)
                
                if fill_time_utc >= start_time_utc:
                    time_filtered_fills.append(fill)
            
            # Get configured products for this account
            product_settings = db.query(AccountProductSettings).filter(
                AccountProductSettings.account_id == account.id
            ).all()
            products = [p.product_symbol.upper() for p in product_settings]
            
            # Get instrument details for filtering
            unique_instrument_ids = set(fill.get('instrumentId') for fill in time_filtered_fills)
            instrument_map = {}
            
            for instrument_id in unique_instrument_ids:
                try:
                    instrument = tt_client.get_instrument_by_id(instrument_id)
                    instrument_map[instrument_id] = instrument
                except Exception as e:
                    logger.warning(f"Failed to get instrument {instrument_id}: {str(e)}")
            
            # Filter by configured products
            for fill in time_filtered_fills:
                instrument_id = fill.get('instrumentId')
                
                # Check if instrument matches configured products
                if instrument_id in instrument_map:
                    instrument = instrument_map[instrument_id]
                    symbol = instrument.get('alias', '').upper()
                    product_symbol = instrument.get('productSymbol', '').upper()
                    
                    matches_product = False
                    for configured_product in products:
                        if (configured_product in symbol or 
                            configured_product in product_symbol or 
                            configured_product == instrument_id):
                            matches_product = True
                            break
                    
                    if not matches_product:
                        continue
                else:
                    continue

                # Check if fill already exists
                exec_id = _get_exec_id(fill)
                if _is_duplicate_fill(db, exec_id, _compute_row_hash(fill)):
                    skipped_count += 1
                    continue

                db.add(_build_fill_record(account, fill))
                synced_count += 1
                
                # Update position (FIFO)
                _update_position_fifo(db, account, fill, instrument_map.get(instrument_id))
                updated_count += 1
        
        db.commit()
        
        return {
            "message": "Fills synced successfully",
            "accounts_processed": len(accounts_with_products),
            "days_back": days_back,
            "time_range": f"{start_time_ist.strftime('%Y-%m-%d %H:%M:%S IST')} to {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}",
            "fills_synced": synced_count,
            "fills_skipped": skipped_count,
            "positions_updated": updated_count
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error syncing fills: {str(e)}")


def _get_exec_id(fill: dict) -> str:
    """
    TT doesn't always populate execId (seen on some report/exec types, e.g.
    execType 14 with no allocation leg). Fill.exec_id is UNIQUE + NOT NULL,
    so fall back to other TT-provided identifiers rather than inserting NULL.
    """
    return (
        fill.get('execId')
        or fill.get('recordId')
        or fill.get('uniqueExecId')
        or f"{fill.get('instrumentId')}-{fill.get('transactTime')}-{fill.get('accountId')}"
    )


def _compute_row_hash(fill: dict) -> str:
    """
    SHA-256 of the full raw TT payload, keys sorted for a stable digest.
    Two fills hash the same only if every field is identical — the "whole
    row is the same" duplicate check, independent of (and stricter than)
    matching on exec_id alone.
    """
    canonical = json.dumps(fill, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_duplicate_fill(db: Session, exec_id: str, row_hash: str) -> bool:
    """A fill is skipped if either its exec_id or its full-row hash already exists."""
    return db.query(Fill).filter((Fill.exec_id == exec_id) | (Fill.row_hash == row_hash)).first() is not None


def _build_fill_record(account: Account, fill: dict) -> Fill:
    """Build a Fill ORM record from a raw TT fill dict (not yet added/committed)"""
    return Fill(
        account_id=account.id,
        account_name=fill.get('account') or account.name,
        tt_account_id=fill.get('accountId') or account.tt_account_id,
        aggressor_indicator=fill.get('aggressorIndicator'),
        algo_id=str(fill.get('algoId')) if fill.get('algoId') is not None else None,
        alloc_id=fill.get('allocId'),
        avg_px=fill.get('avgPx'),
        broker_id=fill.get('brokerId'),
        client_ip=fill.get('clientIP'),
        cum_qty=fill.get('cumQty'),
        curr_user_id=str(fill.get('currUserId')) if fill.get('currUserId') is not None else None,
        delta_qty=fill.get('deltaQty'),
        exch_leaves_qty=fill.get('exchLeavesQty'),
        exch_order_assoc=fill.get('exchOrderAssoc'),
        exec_id=_get_exec_id(fill),
        exec_inst=fill.get('execInst'),
        exec_type=fill.get('execType'),
        externally_created=fill.get('externallyCreated'),
        handling_instr=fill.get('handlingInstr'),
        instrument_id=fill.get('instrumentId'),
        last_px=fill.get('lastPx'),
        last_qty=fill.get('lastQty'),
        manual_fill=fill.get('manualFill'),
        manual_order_indicator=fill.get('manualOrderIndicator'),
        market_id=fill.get('marketId'),
        message_type=fill.get('messageType'),
        multi_leg_reporting_type=fill.get('multiLegReportingType'),
        ord_status=fill.get('ordStatus'),
        ord_type=fill.get('ordType'),
        order_cross_prevention_type=fill.get('orderCrossPreventionType'),
        order_id=fill.get('orderId'),
        parent_instrument_id=fill.get('parentInstrumentId'),
        parent_order_id=fill.get('parentOrderId'),
        position_effect=fill.get('positionEffect'),
        record_id=fill.get('recordId'),
        report_id=fill.get('reportId'),
        report_type=fill.get('reportType'),
        secondary_cl_ord_id=fill.get('secondaryClOrdId'),
        secondary_exec_id=fill.get('secondaryExecId'),
        secondary_order_id=fill.get('secondaryOrderId'),
        security_desc=fill.get('securityDesc'),
        sender_location_id=fill.get('senderLocationId'),
        sender_sub_id=fill.get('senderSubId'),
        side=fill.get('side'),
        source=fill.get('source'),
        synthetic_type=fill.get('syntheticType'),
        text_a=fill.get('textA'),
        text_b=fill.get('textB'),
        text_c=fill.get('textC'),
        text_tt=fill.get('textTT'),
        time_in_force=fill.get('timeInForce'),
        time_sent_client=fill.get('timeSentClient'),
        time_sent_tt=fill.get('timeSentTT'),
        time_stamp=fill.get('timeStamp'),
        trade_date=fill.get('tradeDate'),
        trade_match_id=fill.get('tradeMatchId'),
        trade_type=fill.get('tradeType'),
        trading_venue_trade_id=fill.get('tradingVenueTradeId'),
        transact_time=fill.get('transactTime'),
        transaction_type=fill.get('transactionType'),
        unique_exec_id=fill.get('uniqueExecId'),
        fills_group=fill.get('fillsGroup'),
        parties=fill.get('parties'),
        report_sides=fill.get('reportSides'),
        # Full, unmodified payload — every field TT sent, including any not
        # mapped to a column above (present or future).
        raw=fill,
        row_hash=_compute_row_hash(fill)
    )


def _fetch_and_save_fills(db: Session, tt_client, account: Account, start_ns: int, end_ns: int) -> tuple[list, int]:
    """
    Fetch fills for one account within [start_ns, end_ns] directly from TT
    (scoped by accountId, no product filter), save any new ones (deduped —
    see _is_duplicate_fill), and return (fills_in_range, saved_count).
    Doesn't commit — caller controls the transaction.
    """
    try:
        tt_fills = tt_client.get_all_fills(
            start_time_ns=start_ns,
            end_time_ns=end_ns,
            accountID=account.tt_account_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching fills from TT: {str(e)}")

    # Filter to the exact range again client-side, since TT doesn't reliably
    # honor minTimestamp/maxTimestamp server-side.
    range_fills = [
        fill for fill in tt_fills
        if start_ns <= int(fill.get('transactTime', '0')) <= end_ns
    ]
    range_fills.sort(key=lambda f: int(f.get('transactTime', '0')))

    saved_count = 0
    for fill in range_fills:
        exec_id = _get_exec_id(fill)
        if _is_duplicate_fill(db, exec_id, _compute_row_hash(fill)):
            continue

        db.add(_build_fill_record(account, fill))
        saved_count += 1

    return range_fills, saved_count


def sync_fills_for_account(db: Session, tt_client, account: Account, start_date: str, end_date: Optional[str] = None) -> dict:
    """
    Fetch fills for a single account over a calendar-date range directly from
    TT, save any new ones, and return that range's fills. Shared by the
    /sync-account route and the initial backfill triggered when an account
    is created.

    Args:
        start_date: Start of the range, in YYYY-MM-DD (interpreted as IST)
        end_date: End of the range, in YYYY-MM-DD (interpreted as IST); defaults to start_date
    """
    from routes.TT_routes import ist_date_to_ns

    end_date = end_date or start_date

    start_ns = ist_date_to_ns(start_date)
    end_ns = ist_date_to_ns(end_date, end_of_day=True)

    try:
        range_fills, saved_count = _fetch_and_save_fills(db, tt_client, account, start_ns, end_ns)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error saving fills: {str(e)}")

    return {
        "account_name": account.name,
        "start_date": start_date,
        "end_date": end_date,
        "total_fills": len(range_fills),
        "fills_saved": saved_count,
        # Return every field TT gave us for each fill, not a trimmed subset,
        # so the frontend can show/filter/sort on any of them.
        "fills": range_fills
    }


def _trading_day_window_ns() -> tuple[int, int, datetime, datetime]:
    """
    The current trading-day window: the configured trading-day start time
    (TT_routes.TRADING_DAY_START_HOUR/MINUTE, env-configurable via
    TRADING_DAY_START_TIME — the same boundary ist_date_to_ns anchors PNL
    start-date filtering to, so a scheduled sync and a PNL calculation
    always agree on what a given trading day covers) through now. Before
    that time IST, that's still "yesterday's" trading day, so the window
    starts at yesterday's start time instead.
    """
    from routes.TT_routes import TRADING_DAY_START_HOUR, TRADING_DAY_START_MINUTE

    now_ist = datetime.now(IST)
    window_start = now_ist.replace(hour=TRADING_DAY_START_HOUR, minute=TRADING_DAY_START_MINUTE, second=0, microsecond=0)
    if now_ist < window_start:
        window_start -= timedelta(days=1)

    start_ns = int(window_start.timestamp() * 1_000_000_000)
    end_ns = int(now_ist.timestamp() * 1_000_000_000)
    return start_ns, end_ns, window_start, now_ist


def sync_all_accounts_fills(db: Session, tt_client) -> dict:
    """
    Re-fetch every account's fills for the whole current trading day (the
    configured trading-day start time IST through now) and save any new
    ones. This is what the 20-minute background scheduler calls (see
    main.py) to keep fills — and therefore /api/pnl/overview, which reads
    straight from the fills table — up to date without any manual action on
    the Fills page.

    The window is intentionally NOT incremental (not "since last sync") —
    every cycle re-requests the full window. TT can occasionally
    drop a fill from a single response, so re-fetching the whole window each
    time and relying on dedup (see _is_duplicate_fill) to skip what's
    already stored is what actually catches those drops on a later poll.

    One account's failure doesn't abort the rest; each is isolated and
    reported individually.
    """
    start_ns, end_ns, window_start, now_ist = _trading_day_window_ns()

    accounts = db.query(Account).all()
    results = []
    total_saved = 0

    for account in accounts:
        try:
            _, saved_count = _fetch_and_save_fills(db, tt_client, account, start_ns, end_ns)
            db.commit()
            results.append({"account_name": account.name, "status": "ok", "fills_saved": saved_count})
            total_saved += saved_count
        except Exception as e:
            db.rollback()
            logger.error(f"Scheduled fill sync failed for account {account.name}: {e}")
            results.append({"account_name": account.name, "status": "error", "detail": str(e)})

    return {
        "window_start": window_start.strftime('%Y-%m-%d %H:%M:%S IST'),
        "window_end": now_ist.strftime('%Y-%m-%d %H:%M:%S IST'),
        "accounts_processed": len(accounts),
        "total_fills_saved": total_saved,
        "results": results,
    }


@router.post("/sync-account/{account_name}")
def sync_fills_for_account_date_route(
    account_name: str,
    start_date: str,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    from main import tt_client

    account = db.query(Account).filter(Account.name == account_name).first()

    if not account:
        raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")

    return sync_fills_for_account(db, tt_client, account, start_date, end_date)


def _update_position_fifo(db: Session, account: Account, fill: dict, instrument: dict):
    """Update position using FIFO accounting"""
    
    instrument_id = fill.get('instrumentId')
    side = fill.get('side')  # 1=Buy, 2=Sell
    qty = fill.get('lastQty', 0)
    price = fill.get('lastPx', 0)
    
    if not instrument:
        logger.warning(f"No instrument data for {instrument_id}, skipping position update")
        return
    
    # Get or create position
    position = db.query(Position).filter(
        Position.account_id == account.id,
        Position.instrument_id == instrument_id
    ).first()
    
    if not position:
        position = Position(
            account_id=account.id,
            account_name=account.name,
            tt_account_id=account.tt_account_id,
            instrument_id=instrument_id,
            symbol=instrument.get('alias', 'Unknown'),
            product_symbol=instrument.get('productSymbol', ''),
            market_id=instrument.get('marketId'),
            tick_size=instrument.get('tickSize', 1),
            tick_value=instrument.get('tickValue', 1),
            currency=instrument.get('currency', 'USD')
        )
        db.add(position)
    
    tick_size = position.tick_size
    tick_value = position.tick_value
    
    # Update quantities and values
    old_buy_qty = position.buy_qty
    old_sell_qty = position.sell_qty
    old_buy_value = old_buy_qty * position.buy_avg_price if old_buy_qty > 0 else 0
    old_sell_value = old_sell_qty * position.sell_avg_price if old_sell_qty > 0 else 0
    
    if side == 1:  # Buy
        position.buy_qty += qty
        new_buy_value = old_buy_value + (qty * price)
        position.buy_avg_price = new_buy_value / position.buy_qty if position.buy_qty > 0 else 0
    elif side == 2:  # Sell
        position.sell_qty += qty
        new_sell_value = old_sell_value + (qty * price)
        position.sell_avg_price = new_sell_value / position.sell_qty if position.sell_qty > 0 else 0
    
    # Calculate net position
    position.net_position = position.buy_qty - position.sell_qty
    
    # Calculate net open price (FIFO)
    if position.net_position > 0:  # Long
        position.net_open_price = position.buy_avg_price
    elif position.net_position < 0:  # Short
        position.net_open_price = position.sell_avg_price
    else:  # Flat
        position.net_open_price = 0
    
    # Calculate realized PNL (matched quantity)
    matched_qty = min(position.buy_qty, position.sell_qty)
    if matched_qty > 0:
        price_diff = position.sell_avg_price - position.buy_avg_price
        position.realized_pnl = (price_diff / tick_size) * tick_value * matched_qty
    else:
        position.realized_pnl = 0
    
    # Unrealized PNL uses current price (last fill price)
    if position.net_position != 0:
        if position.net_position > 0:  # Long
            price_diff = price - position.net_open_price
        else:  # Short
            price_diff = position.net_open_price - price
        
        position.unrealized_pnl = (price_diff / tick_size) * tick_value * abs(position.net_position)
    else:
        position.unrealized_pnl = 0
    
    position.total_pnl = position.realized_pnl + position.unrealized_pnl
    position.last_updated = datetime.now(IST)


@router.get("/fills/{account_name}")
def get_fills_for_account(
    account_name: str,
    days_back: int = 1,
    db: Session = Depends(get_db)
):
    """Get fills for an account from database"""
    
    account = db.query(Account).filter(Account.name.contains(account_name)).first()
    
    if not account:
        raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")
    
    # Calculate time range
    now_ist = datetime.now(IST)
    start_time_ist = now_ist - timedelta(days=days_back)
    start_time_utc = start_time_ist.astimezone(pytz.UTC)
    
    # Query fills
    fills = db.query(Fill).filter(
        Fill.account_id == account.id
    ).all()
    
    # Filter by time
    filtered_fills = []
    for fill in fills:
        fill_timestamp = int(fill.transact_time)
        fill_time_utc = datetime.fromtimestamp(fill_timestamp / 1_000_000_000, tz=pytz.UTC)
        
        if fill_time_utc >= start_time_utc:
            filtered_fills.append(fill)
    
    return {
        "account_name": account.name,
        "days_back": days_back,
        "total_fills": len(filtered_fills),
        # Return the full stored TT payload per fill (falls back to the
        # trimmed field set only for rows saved before 'raw' was captured).
        "fills": [f.raw if f.raw is not None else {
            "exec_id": f.exec_id,
            "instrument_id": f.instrument_id,
            "symbol": f.security_desc,
            "side": f.side,
            "qty": f.last_qty,
            "price": f.last_px,
            "transact_time": f.transact_time
        } for f in filtered_fills]
    }