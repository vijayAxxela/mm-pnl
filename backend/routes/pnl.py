# routes/pnl.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import List, Optional
from database.db import get_db, Account, AccountProductSettings, Position, Product, ProductFamily, Fill, Market
from datetime import datetime
from collections import defaultdict, deque
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

class ProductSettingCreate(BaseModel):
    product_symbol: str

class ProductSettingResponse(BaseModel):
    id: int
    account_id: int
    product_symbol: str
    
    class Config:
        from_attributes = True

class AccountProductSettingsResponse(BaseModel):
    account_name: str
    account_id: int
    products: List[str]

class PNLOverviewRow(BaseModel):
    account_name: str
    exchange: str
    product_symbol: str
    contract: str
    instrument_id: str
    # Today's activity only (current trading day, same boundary the fill-sync
    # scheduler uses — TRADING_DAY_START_TIME), NOT the full pnl_start_date
    # history. open_qty below stays cumulative — buy_qty - sell_qty will not
    # generally equal open_qty, by design.
    buy_qty: float
    sell_qty: float
    open_qty: float
    # Average entry price of whatever lots are still open after FIFO
    # matching (realized_pnl below is computed via strict FIFO, not
    # weighted-average costing — see get_pnl_overview). If more than one
    # unmatched lot remains at different prices, this is their qty-weighted
    # average purely for display; it plays no part in the realized PNL math
    # itself. In DISPLAY price terms (raw price x DisplayFactor) — i.e. the
    # same price a user sees in the TT app, not TT's internal/raw price.
    avg_open_price: float
    realized_pnl: float  # USD, computed via strict FIFO lot matching
    # Exposed so the frontend can turn a user-entered current price into
    # unrealized PNL itself, without a round trip — there's no live price
    # feed to compute this server-side, so the price is always a manual
    # client-side input. tick_size here is already scaled by DisplayFactor
    # to match avg_open_price's units, so (currentPrice - avg_open_price) /
    # tick_size * tick_value works directly against a user-typed DISPLAY
    # price with no further conversion needed on the frontend.
    tick_size: float
    tick_value: float
    usd_rate: float
    # Snapshot of this contract's realized_pnl taken 6:45 AM IST today (see
    # snapshot_day_open_pnl) — unrealized PNL isn't included as of now.
    # 0.0 if no snapshot exists yet for today.
    day_open_pnl: float = 0.0

@router.post("/settings/{account_name}/products", response_model=ProductSettingResponse)
def add_product_to_account(
    account_name: str,
    product: ProductSettingCreate,
    db: Session = Depends(get_db)
):
    """Add a product to account's PNL tracking list"""
    
    # Get account
    account = db.query(Account).filter(Account.name.contains(account_name)).first()
    
    if not account:
        raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")
    
    existing = db.query(AccountProductSettings).filter(
        AccountProductSettings.account_id == account.id,
        AccountProductSettings.product_symbol == product.product_symbol.upper()
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=400, 
            detail=f"Product '{product.product_symbol}' already exists for this account"
        )
    
    # Add product
    new_setting = AccountProductSettings(
        account_id=account.id,
        product_symbol=product.product_symbol.upper()
    )
    
    db.add(new_setting)
    db.commit()
    db.refresh(new_setting)
    
    return new_setting

@router.get("/settings/{account_name}/products", response_model=AccountProductSettingsResponse)
def get_account_products(account_name: str, db: Session = Depends(get_db)):
    """Get all products configured for an account"""
    
    account = db.query(Account).filter(Account.name.contains(account_name)).first()
    
    if not account:
        raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")
    
    products = db.query(AccountProductSettings).filter(
        AccountProductSettings.account_id == account.id
    ).all()
    
    return {
        "account_name": account.name,
        "account_id": account.tt_account_id,
        "products": [p.product_symbol for p in products]
    }

@router.delete("/settings/{account_name}/products/{product_symbol}")
def delete_product_from_account(
    account_name: str,
    product_symbol: str,
    db: Session = Depends(get_db)
):
    """Remove a product from account's PNL tracking list"""
    
    account = db.query(Account).filter(Account.name.contains(account_name)).first()
    
    if not account:
        raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")
    
    product_setting = db.query(AccountProductSettings).filter(
        AccountProductSettings.account_id == account.id,
        AccountProductSettings.product_symbol == product_symbol.upper()
    ).first()
    
    if not product_setting:
        raise HTTPException(
            status_code=404,
            detail=f"Product '{product_symbol}' not found for this account"
        )
    
    db.delete(product_setting)
    db.commit()
    
    return {"message": f"Product '{product_symbol}' removed from account '{account.name}'"}

@router.put("/settings/{account_name}/products", response_model=AccountProductSettingsResponse)
def update_account_products(
    account_name: str,
    products: List[str],
    db: Session = Depends(get_db)
):
    """Replace all products for an account (bulk update)"""
    
    account = db.query(Account).filter(Account.name.contains(account_name)).first()
    
    if not account:
        raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")
    
    # Delete existing products
    db.query(AccountProductSettings).filter(
        AccountProductSettings.account_id == account.id
    ).delete()
    
    # Add new products
    for product_symbol in products:
        new_setting = AccountProductSettings(
            account_id=account.id,
            product_symbol=product_symbol.upper()
        )
        db.add(new_setting)
    
    db.commit()
    
    # Get updated list
    updated_products = db.query(AccountProductSettings).filter(
        AccountProductSettings.account_id == account.id
    ).all()
    
    return {
        "account_name": account.name,
        "account_id": account.tt_account_id,
        "products": [p.product_symbol for p in updated_products]
    }

# routes/pnl.py - Add this endpoint

@router.get("/positions/{account_name}")
def get_positions(account_name: str, db: Session = Depends(get_db)):
    """Get current positions for an account from database"""
    
    account = db.query(Account).filter(Account.name.contains(account_name)).first()
    
    if not account:
        raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")
    
    positions = db.query(Position).filter(Position.account_id == account.id).all()
    
    return {
        "account_name": account.name,
        "total_positions": len(positions),
        "total_realized_pnl": sum(p.realized_pnl for p in positions),
        "total_unrealized_pnl": sum(p.unrealized_pnl for p in positions),
        "total_pnl": sum(p.total_pnl for p in positions),
        "positions": [{
            "instrument_id": p.instrument_id,
            "symbol": p.symbol,
            "product_symbol": p.product_symbol,
            "net_position": p.net_position,
            "net_open_price": p.net_open_price,
            "buy_qty": p.buy_qty,
            "sell_qty": p.sell_qty,
            "buy_avg_price": p.buy_avg_price,
            "sell_avg_price": p.sell_avg_price,
            "realized_pnl": p.realized_pnl,
            "unrealized_pnl": p.unrealized_pnl,
            "total_pnl": p.total_pnl,
            "sod_position": p.sod_position,
            "tick_size": p.tick_size,
            "tick_value": p.tick_value
        } for p in positions]
    }


def _get_or_cache_product_family(db: Session, tt_client, product_id: str) -> ProductFamily:
    """
    Local write-through cache for product-family-level data (ttpds/product) —
    symbol, name, currency — keyed by TT productId. The full raw response is
    kept on the row too, so nothing TT returns for the product is discarded.
    """
    family = db.query(ProductFamily).filter(ProductFamily.id == product_id).first()
    if family:
        return family

    product_info = tt_client.get_product_by_id(product_id)

    family = ProductFamily(
        id=product_id,
        symbol=product_info.get('symbol'),
        name=product_info.get('name'),
        currency_id=str(product_info['currencyId']) if product_info.get('currencyId') is not None else None,
        market_id=product_info.get('marketId'),
        product_type_id=product_info.get('typeId'),  # TT's product-level field is 'typeId', not 'productTypeId'
        raw=product_info
    )
    db.add(family)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with another concurrent request caching the same
        # product family (e.g. two PNL requests for different accounts that
        # share a product, resolved in overlapping threads) — the other one
        # already inserted this id first. Roll back this session's failed
        # insert (leaving the session poisoned otherwise) and use theirs.
        db.rollback()
        existing = db.query(ProductFamily).filter(ProductFamily.id == product_id).first()
        if existing:
            return existing
        raise
    db.refresh(family)
    return family


def _get_or_cache_instrument(db: Session, tt_client, instrument_id: str) -> Product:
    """
    Local write-through cache for contract/instrument-level reference data
    (tick size/value, expiry, term) — the Product table, keyed by TT
    instrument id. Also resolves and caches the parent product family (see
    _get_or_cache_product_family) so product-level info isn't lost either.
    Avoids re-querying TT for the same instrument/product on every PNL view.
    """
    product = db.query(Product).filter(Product.id == instrument_id).first()
    if product:
        return product

    instrument = tt_client.get_instrument_by_id(instrument_id)

    currency_id = None
    product_id = instrument.get('productId')
    if product_id:
        family = _get_or_cache_product_family(db, tt_client, product_id)
        currency_id = family.currency_id

    # TT's tickValue is sometimes wrong — confirmed on HKEX GDU, where TT
    # returns the per-gram tick amount (0.01) instead of the true
    # per-contract value (pointValue x tickSize = 1000 x 0.01 = 10): it
    # never applied the contract's unit-size multiplier. The correct
    # per-contract tick value should always equal pointValue x tickSize, so
    # whenever TT's raw figure disagrees with that, trust the computed one
    # instead and flag the row as adjusted.
    raw_tick_value = instrument.get('tickValue')
    point_value = instrument.get('pointValue')
    tick_size = instrument.get('tickSize')

    tick_value = raw_tick_value
    tick_value_adjusted = False
    if point_value is not None and tick_size is not None:
        computed_tick_value = point_value * tick_size
        if raw_tick_value is None or abs(computed_tick_value - raw_tick_value) > 1e-9:
            tick_value = computed_tick_value
            tick_value_adjusted = True
            logger.warning(
                f"Instrument {instrument_id} ({instrument.get('alias')}): TT tickValue={raw_tick_value!r} "
                f"disagrees with pointValue*tickSize={computed_tick_value!r} — using the computed value."
            )

    product = Product(
        alias=instrument.get('alias'),
        displayFactor=instrument.get('displayFactor'),
        displayType=instrument.get('displayType'),
        expirationDate=instrument.get('expirationDate'),
        id=instrument.get('id'),
        lastTradeDate=instrument.get('lastTradeDate'),
        marketId=instrument.get('marketId'),
        name=instrument.get('name'),
        pointValue=instrument.get('pointValue'),
        productFamilyId=instrument.get('productFamilyId'),
        productId=instrument.get('productId'),
        productSymbol=instrument.get('productSymbol'),
        productTypeId=instrument.get('productTypeId'),
        ricCode=instrument.get('ricCode'),
        roundLotQty=instrument.get('roundLotQty'),
        securityExchange=instrument.get('securityExchange'),
        securityId=instrument.get('securityId'),
        seriesTermId=instrument.get('seriesTermId'),
        term=instrument.get('term'),
        tickSize=tick_size,
        tickSizeDenominator=instrument.get('tickSizeDenominator'),
        tickSizeNumerator=instrument.get('tickSizeNumerator'),
        tickValue=tick_value,
        tick_value_adjusted=tick_value_adjusted,
        currency_id=currency_id,
        raw=instrument
    )
    db.add(product)
    try:
        db.commit()
    except IntegrityError:
        # Same concurrent-cache race as _get_or_cache_product_family above.
        db.rollback()
        existing = db.query(Product).filter(Product.id == instrument_id).first()
        if existing:
            return existing
        raise
    db.refresh(product)
    return product


def _compute_pnl_rows(db: Session, tt_client) -> List[dict]:
    """
    Per-contract PNL, computed from stored fills (from each account's
    pnl_start_date onward): open quantity and realized PNL only. One row per
    (account, instrument) — includes account_id (not part of the public
    PNLOverviewRow schema) so callers like snapshot_day_open_pnl can key
    snapshots by account without a second lookup.

    Unrealized/mark-to-market PNL is deliberately not included — TT's REST
    API doesn't expose a last-traded-price/quote endpoint, so there's no
    reliable live price to mark open positions against; it's computed
    client-side (or by snapshot_day_open_pnl) from a manually-entered price.

    PNL is converted to USD using the current day's cached FX rate (fetched
    from TT at most once per rate day — see TT_routes.get_daily_usd_rate).
    """
    from routes.TT_routes import ist_date_to_ns, get_daily_usd_rate
    from routes.fills import _trading_day_window_ns

    accounts = db.query(Account).all()
    if not accounts:
        return []

    market_names = {m.tt_market_id: m.name for m in db.query(Market).all()}

    # Buy/Sell Qty are today's activity only (same trading-day boundary the
    # fill-sync scheduler uses — TRADING_DAY_START_TIME), not the full
    # pnl_start_date-scoped history everything else here uses. Net stays
    # cumulative, computed from the FIFO open position below as always.
    today_start_ns, _, _, _ = _trading_day_window_ns()
    today_start_str = str(today_start_ns)

    results = []

    for account in accounts:
        query = db.query(Fill).filter(Fill.account_id == account.id)
        if account.pnl_start_date:
            start_ns = ist_date_to_ns(account.pnl_start_date)
            # transact_time is a nanosecond-epoch string; same-length numeric
            # strings compare correctly lexicographically, avoiding a
            # per-row cast at query time.
            query = query.filter(Fill.transact_time >= str(start_ns))

        fills = query.all()

        # Drop individual-leg rows of a multi-leg (spread) trade — FIX tag
        # 442, multiLegReportingType == 2. TT reports a spread fill both as
        # this per-leg breakdown AND as the aggregated multi-leg fill (type
        # 3); counting both would double-count qty/PNL for spread trades.
        # Only excluded from this calculation — still fully stored in the DB.
        fills = [f for f in fills if f.multi_leg_reporting_type != '2']

        if not fills:
            continue

        by_instrument = defaultdict(list)
        for f in fills:
            by_instrument[f.instrument_id].append(f)

        for instrument_id, instrument_fills in by_instrument.items():
            try:
                product = _get_or_cache_instrument(db, tt_client, instrument_id)
            except Exception as e:
                logger.warning(f"Failed to resolve instrument {instrument_id} for PNL: {e}")
                continue

            tick_size = product.tickSize or 0
            tick_value = product.tickValue or 0
            # DisplayFactor converts TT's raw/native price (what fills are
            # stored in) to the price the user actually sees in the TT app —
            # e.g. CME ES: raw price x 0.01 = displayed price. Fills are all
            # in raw terms, so avg_open_price and tick_size are scaled here
            # before being sent to the frontend, where the user types a
            # DISPLAY price as "current price" (it's the only price they can
            # see). tick_value is unaffected: it's $ per tick_size move, and
            # scaling both the price diff and tick_size by the same factor
            # cancels out, leaving the PNL math unchanged.
            display_factor = product.displayFactor or 1.0
            product_symbol = product.productSymbol or "UNKNOWN"
            contract = product.alias or product.name or instrument_id
            exchange = market_names.get(str(product.marketId), str(product.marketId) if product.marketId else "UNKNOWN")

            # FIFO lot matching: walk fills in chronological order, keeping a
            # queue of still-open lots on the current side. A same-side fill
            # opens a new lot; an opposite-side fill closes against the
            # OLDEST open lots first (FIFO), realizing PNL per matched
            # portion at that lot's own entry price — never blended into a
            # running average the way weighted-average costing would. If an
            # opposite-side fill outsizes all open lots, the leftover flips
            # the position and opens new lots on the other side.
            sorted_fills = sorted(instrument_fills, key=lambda f: (f.transact_time, f.id))

            today_fills = [f for f in instrument_fills if f.transact_time >= today_start_str]
            buy_qty = sum(f.last_qty for f in today_fills if f.side == 1)
            sell_qty = sum(f.last_qty for f in today_fills if f.side == 2)

            open_lots = deque()  # [[qty, price], ...] — all same side while non-empty
            open_side = 0  # 1 = long, -1 = short, 0 = flat
            realized_native = 0.0

            # "Is this quantity actually zero" needs a tolerance scaled to
            # the volume being matched, not a fixed constant — floating-point
            # error accumulates with every add/subtract, so a large number of
            # small partial fills against one lot can leave a residual well
            # above a fixed 1e-9 (e.g. ~1e-8 after ~800 fills summing to
            # 1,000,000), which would otherwise register as a phantom
            # leftover position instead of resolving to flat.
            qty_epsilon = max(1e-9, (buy_qty + sell_qty) * 1e-9)

            for f in sorted_fills:
                side = 1 if f.side == 1 else -1
                qty = f.last_qty
                price = f.last_px

                if open_side == 0 or side == open_side:
                    open_lots.append([qty, price])
                    open_side = side
                    continue

                remaining = qty
                while remaining > qty_epsilon and open_lots:
                    lot = open_lots[0]
                    matched = min(remaining, lot[0])
                    price_diff = (price - lot[1]) if open_side == 1 else (lot[1] - price)
                    if tick_size:
                        realized_native += (price_diff / tick_size) * tick_value * matched
                    lot[0] -= matched
                    remaining -= matched
                    if lot[0] <= qty_epsilon:
                        open_lots.popleft()

                if remaining > qty_epsilon:
                    # Fully closed the existing position and flipped it.
                    open_side = side
                    open_lots.append([remaining, price])
                elif not open_lots:
                    open_side = 0

            if open_lots:
                total_open_qty = sum(lot[0] for lot in open_lots)
                avg_open_price = sum(lot[0] * lot[1] for lot in open_lots) / total_open_qty
                open_qty = total_open_qty * open_side
            else:
                avg_open_price = 0.0
                open_qty = 0.0

            usd_rate = get_daily_usd_rate(db, tt_client, product.currency_id)
            realized_usd = realized_native * usd_rate

            results.append({
                "account_id": account.id,
                "account_name": account.name,
                "exchange": exchange,
                "product_symbol": product_symbol,
                "contract": contract,
                "instrument_id": instrument_id,
                "buy_qty": round(buy_qty, 4),
                "sell_qty": round(sell_qty, 4),
                "open_qty": round(open_qty, 4),
                "avg_open_price": round(avg_open_price * display_factor, 6),
                "realized_pnl": round(realized_usd, 2),
                "tick_size": tick_size * display_factor,
                "tick_value": tick_value,
                "usd_rate": usd_rate,
            })

    results.sort(key=lambda r: (r["account_name"], r["exchange"], r["product_symbol"], r["contract"]))
    return results


def snapshot_day_open_pnl(db: Session, tt_client) -> dict:
    """
    Take a snapshot of every contract's PNL and store it as that trading
    day's "day open" baseline. For now this only snapshots realized_pnl —
    unrealized PNL depends on a manually-entered "current price" (see
    PriceInput/PRICES_KEY in PnlTree.jsx) that isn't reliable/expected to be
    kept up to date at snapshot time, so it's deliberately left out until
    that changes.

    Called once daily, 15 minutes after the trading day starts (see
    TT_routes.TRADING_DAY_START_HOUR/MINUTE, env-configurable via
    TRADING_DAY_START_TIME, and main._daily_snapshot_loop), so the PNL page
    can show each contract's "Day Open PNL" as a reference point for
    movement since the day began. Upserts on (account_id, instrument_id,
    snapshot_date), so re-running for the same day is safe/idempotent.
    """
    from routes.TT_routes import IST
    from database.db import DailyPnlSnapshot

    rows = _compute_pnl_rows(db, tt_client)

    snapshot_date = datetime.now(IST).strftime('%Y-%m-%d')
    saved = 0

    for row in rows:
        day_open_pnl = round(row["realized_pnl"], 2)

        existing = db.query(DailyPnlSnapshot).filter(
            DailyPnlSnapshot.account_id == row["account_id"],
            DailyPnlSnapshot.instrument_id == row["instrument_id"],
            DailyPnlSnapshot.snapshot_date == snapshot_date,
        ).first()

        if existing:
            existing.day_open_pnl = day_open_pnl
        else:
            db.add(DailyPnlSnapshot(
                account_id=row["account_id"],
                instrument_id=row["instrument_id"],
                snapshot_date=snapshot_date,
                day_open_pnl=day_open_pnl,
            ))
        saved += 1

    db.commit()
    return {"snapshot_date": snapshot_date, "contracts_snapshotted": saved}


@router.get("/overview", response_model=List[PNLOverviewRow])
def get_pnl_overview(db: Session = Depends(get_db)):
    """
    Public PNL overview endpoint — wraps _compute_pnl_rows and attaches each
    row's "Day Open PNL" (the total-PNL snapshot taken shortly after the
    current trading day starts, if one has been taken yet — see
    snapshot_day_open_pnl), defaulting to 0.0 for a contract with no
    snapshot (e.g. before today's snapshot has run, or a just-opened new
    contract).
    """
    from main import tt_client
    from routes.fills import _trading_day_window_ns
    from database.db import DailyPnlSnapshot

    rows = _compute_pnl_rows(db, tt_client)
    if not rows:
        return []

    # The trading day's own calendar date, NOT plain today's-date-at-midnight
    # — the trading day rolls over at TRADING_DAY_START_TIME (e.g. 6:00 AM
    # IST), same as snapshot_day_open_pnl's own snapshot_date, so between
    # midnight and that time this still needs to look up YESTERDAY's
    # snapshot (still the current trading day) rather than looking for
    # today's, which hasn't been taken yet and would wrongly show 0.
    _, _, window_start, _ = _trading_day_window_ns()
    current_trading_day = window_start.strftime('%Y-%m-%d')
    snapshots = db.query(DailyPnlSnapshot).filter(DailyPnlSnapshot.snapshot_date == current_trading_day).all()
    snapshot_by_key = {(s.account_id, s.instrument_id): s.day_open_pnl for s in snapshots}

    for row in rows:
        row["day_open_pnl"] = snapshot_by_key.get((row["account_id"], row["instrument_id"]), 0.0)

    return rows