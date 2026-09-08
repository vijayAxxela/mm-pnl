# routes/analyze.py
"""
FIFO trade analysis for one or more configured contracts — mirrors the
structure of the manual Excel workbook this replaces (Summary + Trade
Sheet, plus a handful of columns that workbook also had — GDU Lots Traded,
GC range columns).

GDU Lots Traded / GC range enrichment (see _enrich_trades_with_reference_data)
reads from the time_and_sales / ohlc_bars tables — populated via the
Analyze page's "Add New Data" upload (routes/data_upload.py), not fetched
from TT — so a trade whose entry time falls outside whatever's been
uploaded so far is left blank rather than guessed at, same as before any
data existed at all.

Both tables store prices exactly as they appear in the uploaded file — the
same "display" convention this app uses everywhere a price reaches the
frontend (raw exchange price x Product.displayFactor; see entry_price/
exit_price below). Converting a GC High-Low range into ticks therefore
needs the DISPLAY tick size (Product.tickSize x Product.displayFactor),
not the raw tickSize alone — confirmed against this deployment's cached GC
Dec26 product (tickSize=1.0, displayFactor=0.1 -> display tick size 0.1,
which matches the reference workbook's hardcoded GC_TICK_SIZE=0.1).

Which contract(s) to analyze, and how far back, are read from
"analyze utils/analyze.json" (see _load_analyze_config) — not hardcoded —
so pointing this at different contracts or a different date range is a
config edit, not a code change.

Scope:
  - Only the contract(s) listed in analyze.json's "contracts" array — each
    matched by EXACT alias, so listing an outright's name never picks up a
    same-underlying calendar spread (a different alias entirely, e.g.
    "GDU Dec26-Feb27 Calendar") — nor vice versa. Put a spread's own alias
    in the array if you want it included too; nothing about the matching
    assumes "outright only".
  - multiLegReportingType == '2' fills are always dropped — same per-leg-
    vs-aggregate double count this app avoids everywhere else (see
    fills.py/pnl.py). This still applies to a spread you've listed
    directly: its own aggregate fills (multiLegReportingType == '3') are
    kept, only the per-leg breakdown rows are dropped.
"""
import bisect
import json
import logging
import math
import os
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db, Account, Fill, OhlcBar, Product, TimeAndSales
from routes.TT_routes import IST

router = APIRouter()
logger = logging.getLogger(__name__)

# "analyze utils" sits next to backend/ (see backend/analyze utils/), not
# under routes/ — same directory routes/pnl.py's backfill script and the
# reference workbook already live in.
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "analyze utils", "analyze.json")


def _load_analyze_config() -> dict:
    """
    Read fresh on every request (not cached) — this file is meant to be
    hand-edited (contracts, start_date) without a restart. Falls back to {}
    on anything wrong with the file rather than 500ing the whole endpoint;
    the caller decides what a missing key means.
    """
    try:
        with open(_CONFIG_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        logger.warning(f"analyze.json is not valid JSON ({e}) — ignoring, using defaults")
        return {}


class TradeRow(BaseModel):
    contract: str
    entry_time: str
    entry_time_ns: int
    exit_time: str
    exit_time_ns: int
    lots: float
    entry_side: str
    entry_price: float
    exit_price: float
    pnl: float
    duration_ms: int
    exit_fill_count: int
    # Filled in by _enrich_trades_with_reference_data from whatever Time &
    # Sales / OHLC data has been uploaded (see routes/data_upload.py) —
    # left blank (not fabricated) for a trade outside that data's coverage.
    gdu_lots_traded: Optional[float] = None
    gc_10s_range: Optional[float] = None
    gc_1m_range: Optional[float] = None
    gc_5m_range: Optional[float] = None
    gc_30m_range: Optional[float] = None


class SummaryMetric(BaseModel):
    metric: str
    value: Optional[float] = None


class GduAnalysisRow(BaseModel):
    """One row of the reference workbook's "GDU Analysis" sheet — trades
    bucketed by their (rounded) gdu_lots_traded value, see
    _build_gdu_analysis."""

    gdu_lots_traded: int
    trades: int
    total_trade_lots: float
    total_pnl: float
    average_pnl_per_lot: Optional[float] = None
    median_pnl: float
    avg_gc_10s_range: Optional[float] = None
    avg_gc_1m_range: Optional[float] = None
    avg_gc_5m_range: Optional[float] = None
    avg_gc_30m_range: Optional[float] = None


class HalfHourRow(BaseModel):
    """One row of the reference workbook's "Half Hour" sheet — one per
    distinct real half-hour calendar window an entry actually fell in
    (can span many different days), see _build_half_hour."""

    half_hour_start: str
    half_hour_end: str
    pnl: float
    number_of_trades: int
    lots: float
    average_pnl_per_lot: Optional[float] = None
    avg_gc_10s_range: Optional[float] = None
    avg_gc_1m_range: Optional[float] = None
    avg_gc_5m_range: Optional[float] = None
    avg_gc_30m_range: Optional[float] = None


class HalfHourlySummaryRow(BaseModel):
    """One row of the reference workbook's "Cumulative Half Hour" sheet
    (named "Half Hourly Summary" on this page) — one per half-hour TIME OF
    DAY, combining every day's trades in that same slot, see
    _build_half_hourly_summary."""

    time_bucket: str
    pnl: float
    number_of_trades: int
    lots: float
    average_pnl_per_lot: Optional[float] = None
    avg_gc_10s_range: Optional[float] = None
    avg_gc_1m_range: Optional[float] = None
    avg_gc_5m_range: Optional[float] = None
    avg_gc_30m_range: Optional[float] = None


class PnlHistogramBucket(BaseModel):
    """One bar of the reference workbook's "PnL Histogram" sheet, see
    _build_pnl_histogram. color mirrors the workbook's red/green/gray
    coding: "loss" (bucket entirely <= 0), "profit" (entirely >= 0), or
    "mixed" (straddles zero) — computed server-side so the frontend just
    renders it, rather than re-deriving the same rule twice."""

    bucket_start: float
    bucket_end: float
    label: str
    count: int
    color: str


class GduFifoResponse(BaseModel):
    contracts: List[str]
    summary: List[SummaryMetric]
    trades: List[TradeRow]
    gdu_analysis: List[GduAnalysisRow]
    half_hour: List[HalfHourRow]
    half_hourly_summary: List[HalfHourlySummaryRow]
    pnl_histogram: List[PnlHistogramBucket]


def _format_ist(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=IST)
    return dt.strftime("%d-%m-%y %H:%M:%S.%f")[:-3]


def _ns_to_ist_naive(ns: int) -> datetime:
    """Same IST wall-clock conversion as _format_ist, but as a naive
    datetime — for comparing against TimeAndSales/OhlcBar.timestamp, which
    are naive too (parsed straight from an uploaded file's Date/Time
    columns, already IST — nothing in this app's fill pipeline is IST-aware
    at the datetime-object level, only at formatting time)."""
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=IST).replace(tzinfo=None)


def _gc_contract_for(gdu_contract: str) -> Optional[str]:
    """"GDU Dec26" -> "GC Dec26" (same term, gold vs. HKEX-listed metal) —
    only contracts actually named "GDU <term>" get a GC counterpart; a
    spread (e.g. "GDU Dec26-Feb27 Calendar") or a non-GDU contract someone
    lists in analyze.json has no defined mapping and is left blank."""
    parts = gdu_contract.split(None, 1)
    if len(parts) != 2 or parts[0].upper() != "GDU":
        return None
    return f"GC {parts[1]}"


def _prepare_ts_index(rows: List[Tuple[datetime, float]]) -> Tuple[List[datetime], List[float]]:
    rows = sorted(rows, key=lambda r: r[0])
    return [r[0] for r in rows], [r[1] for r in rows]


def _prepare_ohlc_index(rows: List[Tuple[datetime, float, float]]) -> Tuple[List[datetime], List[float], List[float]]:
    rows = sorted(rows, key=lambda r: r[0])
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


def _gdu_lots_traded(entry_dt: datetime, duration_ms: int, timestamps: List[datetime], qtys: List[float]) -> Optional[float]:
    """Same rule as the reference workbook's find_gdu_lots: for a trade
    that took measurable time to fill (>100ms), sum every Time & Sales
    print within +/-200ms of the entry — the entry likely traded alongside
    (or was itself made up of) several prints, not just one. For an
    effectively-instant fill (or if that window is empty), fall back to the
    single nearest print, only if it's within 1 second — otherwise there's
    nothing close enough to call a match."""
    if not timestamps:
        return None

    if duration_ms and duration_ms > 100:
        start = entry_dt - timedelta(milliseconds=200)
        end = entry_dt + timedelta(milliseconds=200)
        lo = bisect.bisect_left(timestamps, start)
        hi = bisect.bisect_right(timestamps, end)
        if hi > lo:
            return sum(qtys[lo:hi])

    idx = bisect.bisect_left(timestamps, entry_dt)
    candidates = [i for i in (idx - 1, idx) if 0 <= i < len(timestamps)]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda i: abs((timestamps[i] - entry_dt).total_seconds()))
    if abs((timestamps[nearest] - entry_dt).total_seconds()) <= 1.0:
        return qtys[nearest]
    return None


def _floor_to_window(dt: datetime, window_seconds: int) -> datetime:
    epoch = datetime(1970, 1, 1)
    total_seconds = (dt - epoch).total_seconds()
    floored = total_seconds - (total_seconds % window_seconds)
    return epoch + timedelta(seconds=floored)


def _gc_range_ticks(
    entry_dt: datetime, window_seconds: int, timestamps: List[datetime], highs: List[float], lows: List[float], display_tick_size: float
) -> Optional[float]:
    """High-Low range (in ticks) across every GC bar in the FIXED window
    containing entry_dt — e.g. for 10s windows, entry 10:23:28 uses the
    10:23:20-10:23:29.999 bucket, same fixed-bucket convention (not a
    centered/rolling window) as the reference workbook's get_gc_range."""
    if not timestamps or not display_tick_size:
        return None

    start = _floor_to_window(entry_dt, window_seconds)
    end = start + timedelta(seconds=window_seconds)
    lo = bisect.bisect_left(timestamps, start)
    hi = bisect.bisect_left(timestamps, end)
    if hi <= lo:
        return None

    # round() to shake off float noise from dividing by a fractional tick
    # size (e.g. 0.1) — (4477.9 - 4477.2) / 0.1 lands on 6.999999999999993,
    # not a clean 7.0, purely from float representation, not real data.
    return round((max(highs[lo:hi]) - min(lows[lo:hi])) / display_tick_size, 4)


def _enrich_trades_with_reference_data(db: Session, trades: List[dict]) -> None:
    """Mutates each trade dict in place, filling gdu_lots_traded and the
    four GC range columns from whatever's been uploaded via the Analyze
    page's "Add New Data" flow so far (see routes/data_upload.py) — a
    contract or time window with no matching uploaded data is left blank."""
    if not trades:
        return

    gdu_contracts = {t["contract"] for t in trades}

    ts_rows = (
        db.query(TimeAndSales.contract, TimeAndSales.timestamp, TimeAndSales.qty)
        .filter(TimeAndSales.contract.in_(gdu_contracts))
        .all()
    )
    ts_by_contract: Dict[str, list] = defaultdict(list)
    for contract, timestamp, qty in ts_rows:
        ts_by_contract[contract].append((timestamp, qty))
    ts_index = {contract: _prepare_ts_index(rows) for contract, rows in ts_by_contract.items()}

    gc_contract_for = {c: _gc_contract_for(c) for c in gdu_contracts}
    gc_contracts_needed = {v for v in gc_contract_for.values() if v}

    ohlc_index: Dict[str, tuple] = {}
    gc_display_tick_size: Dict[str, float] = {}
    if gc_contracts_needed:
        ohlc_rows = (
            db.query(OhlcBar.contract, OhlcBar.timestamp, OhlcBar.high, OhlcBar.low)
            .filter(OhlcBar.contract.in_(gc_contracts_needed))
            .all()
        )
        ohlc_by_contract: Dict[str, list] = defaultdict(list)
        for contract, timestamp, high, low in ohlc_rows:
            ohlc_by_contract[contract].append((timestamp, high, low))
        ohlc_index = {contract: _prepare_ohlc_index(rows) for contract, rows in ohlc_by_contract.items()}

        # GC's OHLC prices are stored exactly as uploaded — the same
        # "display" convention as every price this app shows (raw x
        # displayFactor; see this file's module docstring) — so the range
        # must be divided by the DISPLAY tick size, not the raw one.
        gc_products = db.query(Product).filter(Product.alias.in_(gc_contracts_needed)).all()
        for p in gc_products:
            if p.tickSize:
                gc_display_tick_size[p.alias] = p.tickSize * (p.displayFactor or 1.0)

    for t in trades:
        contract = t["contract"]
        entry_dt = _ns_to_ist_naive(t["entry_time_ns"])

        timestamps, qtys = ts_index.get(contract, ([], []))
        gdu_lots = _gdu_lots_traded(entry_dt, t["duration_ms"], timestamps, qtys)
        t["gdu_lots_traded"] = round(gdu_lots) if gdu_lots is not None else None

        gc_contract = gc_contract_for.get(contract)
        tick_size = gc_display_tick_size.get(gc_contract) if gc_contract else None
        gc_timestamps, gc_highs, gc_lows = ohlc_index.get(gc_contract, ([], [], [])) if gc_contract else ([], [], [])

        # Always set (even to None) — trades is a plain dict here, and the
        # caller sums "is not None" over these keys for the summary counts,
        # so a trade with no GC data must still have the key present.
        t["gc_10s_range"] = _gc_range_ticks(entry_dt, 10, gc_timestamps, gc_highs, gc_lows, tick_size) if tick_size else None
        t["gc_1m_range"] = _gc_range_ticks(entry_dt, 60, gc_timestamps, gc_highs, gc_lows, tick_size) if tick_size else None
        t["gc_5m_range"] = _gc_range_ticks(entry_dt, 300, gc_timestamps, gc_highs, gc_lows, tick_size) if tick_size else None
        t["gc_30m_range"] = _gc_range_ticks(entry_dt, 1800, gc_timestamps, gc_highs, gc_lows, tick_size) if tick_size else None


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _avg_range(group: List[dict], key: str) -> Optional[float]:
    """Mean of a GC range column across a group of trades, ignoring trades
    where that column is blank (no matching uploaded OHLC data) — same as
    pandas' .mean() silently skipping NaN in the reference workbook."""
    vals = [t[key] for t in group if t.get(key) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _build_gdu_analysis(trades: List[dict]) -> List[dict]:
    """Same grouping as the reference workbook's "GDU Analysis" sheet
    (create_gdu_analysis in GDU_gc.ipynb): trades are bucketed by their
    (rounded) gdu_lots_traded value, then each bucket is summarized. A
    trade with no gdu_lots_traded (its entry time falls outside whatever
    Time & Sales data has been uploaded) is excluded, same as the
    notebook's dropna(subset=["GDU Lots Traded"])."""
    buckets: Dict[int, List[dict]] = defaultdict(list)
    for t in trades:
        if t.get("gdu_lots_traded") is None:
            continue
        buckets[int(round(t["gdu_lots_traded"]))].append(t)

    rows = []
    for gdu_lots in sorted(buckets):
        group = buckets[gdu_lots]
        total_lots = sum(t["lots"] for t in group)
        total_pnl = sum(t["pnl"] for t in group)
        rows.append({
            "gdu_lots_traded": gdu_lots,
            "trades": len(group),
            "total_trade_lots": round(total_lots, 4),
            "total_pnl": round(total_pnl, 2),
            "average_pnl_per_lot": round(total_pnl / total_lots, 2) if total_lots else None,
            "median_pnl": round(_median([t["pnl"] for t in group]), 2),
            "avg_gc_10s_range": _avg_range(group, "gc_10s_range"),
            "avg_gc_1m_range": _avg_range(group, "gc_1m_range"),
            "avg_gc_5m_range": _avg_range(group, "gc_5m_range"),
            "avg_gc_30m_range": _avg_range(group, "gc_30m_range"),
        })
    return rows


def _build_half_hour(trades: List[dict]) -> List[dict]:
    """Same grouping as the reference workbook's "Half Hour" sheet
    (create_half_hour): one row per distinct real half-hour calendar
    window that actually contains a trade's entry — so this can span many
    different days, unlike _build_half_hourly_summary below. Uses every
    trade (no gdu_lots_traded requirement — the GC range columns simply
    average whatever's available within the bucket, same as _avg_range)."""
    buckets: Dict[datetime, List[dict]] = defaultdict(list)
    for t in trades:
        entry_dt = _ns_to_ist_naive(t["entry_time_ns"])
        buckets[_floor_to_window(entry_dt, 1800)].append(t)

    rows = []
    for start in sorted(buckets):
        group = buckets[start]
        total_lots = sum(t["lots"] for t in group)
        pnl = sum(t["pnl"] for t in group)
        rows.append({
            "half_hour_start": start.strftime("%d-%m-%y %H:%M"),
            "half_hour_end": (start + timedelta(minutes=30)).strftime("%d-%m-%y %H:%M"),
            "pnl": round(pnl, 2),
            "number_of_trades": len(group),
            "lots": round(total_lots, 4),
            "average_pnl_per_lot": round(pnl / total_lots, 2) if total_lots else None,
            "avg_gc_10s_range": _avg_range(group, "gc_10s_range"),
            "avg_gc_1m_range": _avg_range(group, "gc_1m_range"),
            "avg_gc_5m_range": _avg_range(group, "gc_5m_range"),
            "avg_gc_30m_range": _avg_range(group, "gc_30m_range"),
        })
    return rows


def _build_half_hourly_summary(trades: List[dict]) -> List[dict]:
    """Same grouping as the reference workbook's "Cumulative Half Hour"
    sheet (create_cumulative_half_hour) — called "Half Hourly Summary" on
    this page. One row per half-hour TIME OF DAY (HH:MM), combining
    trades from every day in the data that fall in that same slot — e.g.
    every 06:30-07:00 entry across every day, in one row."""
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        entry_dt = _ns_to_ist_naive(t["entry_time_ns"])
        bucket = _floor_to_window(entry_dt, 1800).strftime("%H:%M")
        buckets[bucket].append(t)

    rows = []
    for bucket in sorted(buckets):
        group = buckets[bucket]
        total_lots = sum(t["lots"] for t in group)
        pnl = sum(t["pnl"] for t in group)
        rows.append({
            "time_bucket": bucket,
            "pnl": round(pnl, 2),
            "number_of_trades": len(group),
            "lots": round(total_lots, 4),
            "average_pnl_per_lot": round(pnl / total_lots, 2) if total_lots else None,
            "avg_gc_10s_range": _avg_range(group, "gc_10s_range"),
            "avg_gc_1m_range": _avg_range(group, "gc_1m_range"),
            "avg_gc_5m_range": _avg_range(group, "gc_5m_range"),
            "avg_gc_30m_range": _avg_range(group, "gc_30m_range"),
        })
    return rows


# Same candidate widths, in the same priority order, as the reference
# workbook's post-processing script (the cell that rebuilt "PnL Histogram"
# with round bins + red/green/gray coloring on top of the plain 20-bin
# np.histogram() the main pipeline started with) — picks the smallest
# "nice" width that keeps the bin count around 20-30, since this dataset's
# PnL range is wide enough that a fixed width would make far too many
# mostly-empty bins.
_HISTOGRAM_CANDIDATE_WIDTHS = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000]


def _build_pnl_histogram(trades: List[dict]) -> List[dict]:
    """Same approach as the reference workbook's "PnL Histogram" sheet
    (the round-bin-width version, not the plain fixed-20-bin np.histogram()
    the sheet started as): pick a "nice" bin width from a candidate list
    that keeps the bucket count around 20-30, then bucket every trade's
    PnL into it. Each bucket also gets a color, matching the workbook's
    red (all-loss) / green (all-profit) / gray (straddles zero) coding."""
    pnls = [t["pnl"] for t in trades]
    if not pnls:
        return []

    data_range = max(pnls) - min(pnls)
    bin_width = _HISTOGRAM_CANDIDATE_WIDTHS[-1]
    for w in _HISTOGRAM_CANDIDATE_WIDTHS:
        if data_range == 0 or data_range / w <= 30:
            bin_width = w
            break

    lo = math.floor(min(pnls) / bin_width) * bin_width
    hi = math.ceil(max(pnls) / bin_width) * bin_width
    if hi == lo:
        hi = lo + bin_width
    n_bins = int(round((hi - lo) / bin_width))

    counts = [0] * n_bins
    for v in pnls:
        idx = int((v - lo) // bin_width)
        idx = min(max(idx, 0), n_bins - 1)
        counts[idx] += 1

    rows = []
    for i in range(n_bins):
        b_start = lo + i * bin_width
        b_end = b_start + bin_width
        if b_end <= 0:
            color = "loss"
        elif b_start >= 0:
            color = "profit"
        else:
            color = "mixed"
        rows.append({
            "bucket_start": b_start,
            "bucket_end": b_end,
            "label": f"{b_start:g} to {b_end:g}",
            "count": counts[i],
            "color": color,
        })
    return rows


def _run_fifo_trades(fills: List[Fill], tick_size: float, tick_value: float, display_factor: float, contract: str) -> tuple[List[dict], float]:
    """
    Same FIFO-matching approach as pnl.py's _compute_pnl_rows, but instead
    of only accumulating a running realized-PNL total, this keeps each open
    lot's own record and turns it into one finished "trade" the moment it's
    fully closed (never before) — an opposite-side fill can close it over
    several separate fills, whose prices get weighted-averaged into that
    trade's exit price and whose count becomes exit_fill_count.

    Returns (trades, open_lots) — open_lots is the signed quantity still
    open once every fill's been processed: positive for a net long (open
    side B), negative for a net short (open side S), 0.0 if flat.
    """
    sorted_fills = sorted(fills, key=lambda f: (f.transact_time, f.id))
    open_lots = deque()
    open_side = 0
    trades = []
    qty_epsilon = max(1e-9, sum(abs(f.last_qty) for f in fills) * 1e-9)

    for f in sorted_fills:
        side = 1 if f.side == 1 else -1
        qty = f.last_qty
        price = f.last_px
        ts = int(f.transact_time)

        if open_side == 0 or side == open_side:
            open_lots.append({
                "qty": qty, "orig_qty": qty, "price": price, "entry_time": ts, "side": side,
                "exit_notional": 0.0, "exit_qty": 0.0, "exit_fill_count": 0, "last_exit_time": None,
            })
            open_side = side
            continue

        remaining = qty
        while remaining > qty_epsilon and open_lots:
            lot = open_lots[0]
            matched = min(remaining, lot["qty"])
            lot["exit_notional"] += matched * price
            lot["exit_qty"] += matched
            lot["exit_fill_count"] += 1
            lot["last_exit_time"] = ts
            lot["qty"] -= matched
            remaining -= matched

            if lot["qty"] <= qty_epsilon:
                closed = open_lots.popleft()
                weighted_exit = closed["exit_notional"] / closed["exit_qty"]
                price_diff = (weighted_exit - closed["price"]) if closed["side"] == 1 else (closed["price"] - weighted_exit)
                pnl = (price_diff / tick_size) * tick_value * closed["orig_qty"] if tick_size else 0.0
                trades.append({
                    "contract": contract,
                    "entry_time": _format_ist(closed["entry_time"]),
                    "entry_time_ns": closed["entry_time"],
                    "exit_time": _format_ist(closed["last_exit_time"]),
                    "exit_time_ns": closed["last_exit_time"],
                    "lots": closed["orig_qty"],
                    "entry_side": "B" if closed["side"] == 1 else "S",
                    "entry_price": round(closed["price"] * display_factor, 6),
                    "exit_price": round(weighted_exit * display_factor, 6),
                    "pnl": round(pnl, 2),
                    "duration_ms": (closed["last_exit_time"] - closed["entry_time"]) // 1_000_000,
                    "exit_fill_count": closed["exit_fill_count"],
                })

        if remaining > qty_epsilon:
            # Closing fill outsized every open lot — position flips side.
            open_side = side
            open_lots.append({
                "qty": remaining, "orig_qty": remaining, "price": price, "entry_time": ts, "side": side,
                "exit_notional": 0.0, "exit_qty": 0.0, "exit_fill_count": 0, "last_exit_time": None,
            })
        elif not open_lots:
            open_side = 0

    open_qty = sum(lot["qty"] for lot in open_lots)
    open_lots_signed = open_qty * open_side  # open_side is 0/1/-1

    return trades, open_lots_signed


@router.get("/gdu-fifo", response_model=GduFifoResponse)
def get_gdu_fifo_analysis(db: Session = Depends(get_db)):
    """FIFO-matched entry/exit trades for the configured contract(s)' fills, across every account, plus the same summary metrics the source workbook reported."""
    from main import tt_client
    from routes.pnl import _get_or_cache_instrument
    from routes.TT_routes import ist_date_to_ns

    config = _load_analyze_config()
    contracts = config.get("contracts")
    if not contracts:
        raise HTTPException(
            status_code=400,
            detail=f'"contracts" not set (or empty) in {_CONFIG_PATH} — add e.g. {{"contracts": ["GDU Dec26"]}}',
        )
    contracts = set(contracts)

    start_date = config.get("start_date")
    start_ns_str = str(ist_date_to_ns(start_date)) if start_date else None

    instrument_ids = {row[0] for row in db.query(Fill.instrument_id).distinct().all()}

    matched_instrument_ids = set()
    for instrument_id in instrument_ids:
        try:
            product = _get_or_cache_instrument(db, tt_client, instrument_id)
        except Exception as e:
            logger.warning(f"Failed to resolve instrument {instrument_id} for contract analysis: {e}")
            continue
        # Exact alias match against the configured set — not a substring/
        # family match, so e.g. listing "GDU Dec26" never also picks up
        # "GDU Dec26-Feb27 Calendar", and vice versa.
        if product.alias in contracts:
            matched_instrument_ids.add(instrument_id)

    trades: List[dict] = []
    open_lots_total = 0.0
    if matched_instrument_ids:
        accounts = db.query(Account).all()
        for account in accounts:
            query = (
                db.query(Fill)
                .filter(Fill.account_id == account.id)
                .filter(Fill.instrument_id.in_(matched_instrument_ids))
                .filter(Fill.multi_leg_reporting_type != "2")
            )
            if start_ns_str:
                # transact_time is a nanosecond-epoch string — same-length
                # numeric strings compare correctly lexicographically, same
                # convention as pnl.py's pnl_start_date filtering.
                query = query.filter(Fill.transact_time >= start_ns_str)
            fills = query.all()
            if not fills:
                continue

            by_instrument = {}
            for f in fills:
                by_instrument.setdefault(f.instrument_id, []).append(f)

            # FIFO is run separately per (account, instrument) — matching
            # across two different contracts, or two different accounts'
            # positions in the same contract, would net together positions
            # that were never actually the same book.
            for instrument_id, instrument_fills in by_instrument.items():
                product = _get_or_cache_instrument(db, tt_client, instrument_id)
                instrument_trades, instrument_open_lots = _run_fifo_trades(
                    instrument_fills,
                    tick_size=product.tickSize or 0,
                    tick_value=product.tickValue or 0,
                    display_factor=product.displayFactor or 1.0,
                    contract=product.alias or product.name or instrument_id,
                )
                trades.extend(instrument_trades)
                open_lots_total += instrument_open_lots

    trades.sort(key=lambda t: t["entry_time_ns"])
    _enrich_trades_with_reference_data(db, trades)

    raw_fill_count = 0
    if matched_instrument_ids:
        raw_query = (
            db.query(Fill)
            .filter(Fill.instrument_id.in_(matched_instrument_ids))
            .filter(Fill.multi_leg_reporting_type != "2")
        )
        if start_ns_str:
            raw_query = raw_query.filter(Fill.transact_time >= start_ns_str)
        raw_fill_count = raw_query.count()

    matched_trades = len(trades)
    matched_lots = sum(t["lots"] for t in trades)
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    flat = [t["pnl"] for t in trades if t["pnl"] == 0]

    summary = [
        SummaryMetric(metric="Raw fills", value=raw_fill_count),
        SummaryMetric(metric="FIFO matched trades", value=matched_trades),
        SummaryMetric(metric="Matched lots", value=matched_lots),
        # Signed net position left open across every matched (account,
        # instrument) book combined, once every fill above has been FIFO'd
        # through — positive = net long (open side buy), negative = net
        # short (open side sell), 0 = flat.
        # round() alone can leave a signed -0.0 for "flat" (0 * -1) — always
        # show a plain 0 rather than "-0.00".
        SummaryMetric(metric="Open Lots", value=round(open_lots_total, 4) or 0.0),
        SummaryMetric(metric="Total PnL", value=round(total_pnl, 2)),
        SummaryMetric(metric="Average PnL / Trade", value=round(total_pnl / matched_trades, 2) if matched_trades else None),
        SummaryMetric(metric="Average PnL / Lot", value=round(total_pnl / matched_lots, 2) if matched_lots else None),
        SummaryMetric(metric="Winning Trades", value=len(wins)),
        SummaryMetric(metric="Losing Trades", value=len(losses)),
        SummaryMetric(metric="Flat Trades", value=len(flat)),
        SummaryMetric(metric="Win Rate", value=round(len(wins) / matched_trades, 6) if matched_trades else None),
        SummaryMetric(metric="Average Win", value=round(sum(wins) / len(wins), 2) if wins else None),
        SummaryMetric(metric="Average Loss", value=round(sum(losses) / len(losses), 2) if losses else None),
        SummaryMetric(metric="Trades with GC 10s Range", value=sum(1 for t in trades if t["gc_10s_range"] is not None)),
        SummaryMetric(metric="Trades with GC 1m Range", value=sum(1 for t in trades if t["gc_1m_range"] is not None)),
        SummaryMetric(metric="Trades with GC 5m Range", value=sum(1 for t in trades if t["gc_5m_range"] is not None)),
        SummaryMetric(metric="Trades with GC 30m Range", value=sum(1 for t in trades if t["gc_30m_range"] is not None)),
    ]

    gdu_analysis = _build_gdu_analysis(trades)
    half_hour = _build_half_hour(trades)
    half_hourly_summary = _build_half_hourly_summary(trades)
    pnl_histogram = _build_pnl_histogram(trades)

    return GduFifoResponse(
        contracts=sorted(contracts),
        summary=summary,
        trades=[TradeRow(**t) for t in trades],
        gdu_analysis=[GduAnalysisRow(**r) for r in gdu_analysis],
        half_hour=[HalfHourRow(**r) for r in half_hour],
        half_hourly_summary=[HalfHourlySummaryRow(**r) for r in half_hourly_summary],
        pnl_histogram=[PnlHistogramBucket(**r) for r in pnl_histogram],
    )
