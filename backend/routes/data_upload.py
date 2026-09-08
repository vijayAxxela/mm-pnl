# routes/data_upload.py
"""
Upload endpoint for the raw reference data the Analyze page's FIFO/GC
enrichment will eventually read from the DB instead of the manual
GDUTS.xlsx / "GC OHLC 10sec.xlsx" workbooks (see routes/analyze.py's
docstring and GDU_gc.ipynb) — GDU time & sales prints and GC OHLC bars.
No analysis happens here; this route only parses an uploaded csv/xlsx file
and saves new rows into the (contract-agnostic) time_and_sales / ohlc_bars
tables (see database/db.py), so any contract's data can be added over time.

A single upload can be a lot of rows (the sample "GC OHLC 10sec.xlsx" alone
is 7MB / tens of thousands of 10-second bars), so parsing + the DB diff run
in a background thread (mirrors main.py's _run_scheduled_fill_sync pattern)
while progress is pushed to every connected client over the existing
ws.broadcast channel — the frontend's global upload banner listens for
"data_upload_progress" messages keyed by job_id and stays visible across a
page navigation, since it isn't part of any one page's component tree.

Dedup on reupload is by exact row identity, not by time range: a row is
"new" if its identity tuple isn't already in the table, regardless of
whether it falls before, after, or in between the data already there.
"""
import asyncio
import io
import logging
import uuid
import warnings
from typing import Optional

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from database.db import OhlcBar, SessionLocal, TimeAndSales
from routes import ws

router = APIRouter()
logger = logging.getLogger(__name__)

INSERT_BATCH_SIZE = 2000


# ============================================================
# Column name resolution — uploaded files aren't guaranteed to use the
# exact header text the sample workbooks happen to use, so match a small
# set of case-insensitive aliases per logical column instead of hardcoding
# one spelling.
# ============================================================

_TS_DATE_ALIASES = ["date"]
_TS_TIME_ALIASES = ["time"]
_TS_DATETIME_ALIASES = ["datetime", "timestamp"]
_TS_CONTRACT_ALIASES = ["contract", "symbol"]
_TS_QTY_ALIASES = ["qty", "quantity", "lots", "size"]
_TS_PRICE_ALIASES = ["price"]

_OHLC_DATETIME_ALIASES = ["dates", "date", "datetime", "timestamp"]
_OHLC_CONTRACT_ALIASES = ["contract", "symbol"]
_OHLC_OPEN_ALIASES = ["open"]
_OHLC_HIGH_ALIASES = ["high"]
_OHLC_LOW_ALIASES = ["low"]
_OHLC_CLOSE_ALIASES = ["close"]
_OHLC_VOLUME_ALIASES = ["volume", "vol"]


def _resolve_column(columns_lower: dict, aliases: list) -> Optional[str]:
    for alias in aliases:
        if alias in columns_lower:
            return columns_lower[alias]
    return None


def _read_any(raw_bytes: bytes, filename: str, sheet_name=0) -> pd.DataFrame:
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw_bytes), sheet_name=sheet_name)
    return pd.read_csv(io.BytesIO(raw_bytes))


def _none_if_nan(value):
    return None if value is None or (isinstance(value, float) and value != value) else value


# ============================================================
# Parsing
# ============================================================

def parse_time_sales(raw_bytes: bytes, filename: str) -> pd.DataFrame:
    """Returns columns: contract, timestamp, qty, price. Only the first
    sheet is read for an xlsx — contract is a per-row column here, not a
    per-sheet split like the OHLC file, so there's no reason to expect (or
    combine) more than one sheet."""
    df = _read_any(raw_bytes, filename)
    columns_lower = {str(c).strip().lower(): c for c in df.columns}

    contract_col = _resolve_column(columns_lower, _TS_CONTRACT_ALIASES)
    qty_col = _resolve_column(columns_lower, _TS_QTY_ALIASES)
    price_col = _resolve_column(columns_lower, _TS_PRICE_ALIASES)
    datetime_col = _resolve_column(columns_lower, _TS_DATETIME_ALIASES)
    date_col = _resolve_column(columns_lower, _TS_DATE_ALIASES)
    time_col = _resolve_column(columns_lower, _TS_TIME_ALIASES)

    if not contract_col or not qty_col:
        raise ValueError("Time & Sales file must have a Contract column and a Qty/Lots column.")
    if not datetime_col and not (date_col and time_col):
        raise ValueError("Time & Sales file must have either a single Date/Time column or separate Date and Time columns.")

    out = pd.DataFrame()
    # Uploaded files can use any date format ("03Sep26", "2026-09-03", ...)
    # depending on where the export came from, so format is intentionally
    # left for pandas to infer per-element rather than assumed — silencing
    # the resulting "falling back to dateutil" warning that inference emits.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        if datetime_col:
            out["timestamp"] = pd.to_datetime(df[datetime_col], errors="coerce")
        else:
            date_text = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
            time_text = df[time_col].astype(str)
            out["timestamp"] = pd.to_datetime(date_text + " " + time_text, errors="coerce")

    out["contract"] = df[contract_col].astype(str).str.strip()
    out["qty"] = pd.to_numeric(df[qty_col], errors="coerce")
    out["price"] = pd.to_numeric(df[price_col], errors="coerce") if price_col else None

    out = out.dropna(subset=["timestamp", "contract", "qty"])
    out = out[out["contract"] != ""]
    return out.reset_index(drop=True)


def parse_ohlc(raw_bytes: bytes, filename: str, fallback_contract: Optional[str]) -> pd.DataFrame:
    """Returns columns: contract, timestamp, open, high, low, close, volume.
    An xlsx may have one sheet per contract (as "GC OHLC 10sec.xlsx" does —
    "GC Dec26", "GC Aug26"), so every sheet is read and the sheet name used
    as that sheet's contract unless the sheet itself has a Contract column.
    A csv has no sheets, so it needs either a Contract column or the
    fallback_contract the caller supplied."""
    is_excel = filename.lower().endswith((".xlsx", ".xls"))
    frames = []

    if is_excel:
        excel_file = pd.ExcelFile(io.BytesIO(raw_bytes))
        sheet_names = excel_file.sheet_names
        sheets = {name: excel_file.parse(name) for name in sheet_names}
    else:
        sheets = {None: pd.read_csv(io.BytesIO(raw_bytes))}

    for sheet_name, df in sheets.items():
        columns_lower = {str(c).strip().lower(): c for c in df.columns}

        datetime_col = _resolve_column(columns_lower, _OHLC_DATETIME_ALIASES)
        high_col = _resolve_column(columns_lower, _OHLC_HIGH_ALIASES)
        low_col = _resolve_column(columns_lower, _OHLC_LOW_ALIASES)
        if not datetime_col or not high_col or not low_col:
            raise ValueError(
                f"OHLC file{f' sheet {sheet_name!r}' if sheet_name else ''} must have Date/Dates, High, and Low columns."
            )

        open_col = _resolve_column(columns_lower, _OHLC_OPEN_ALIASES)
        close_col = _resolve_column(columns_lower, _OHLC_CLOSE_ALIASES)
        volume_col = _resolve_column(columns_lower, _OHLC_VOLUME_ALIASES)
        contract_col = _resolve_column(columns_lower, _OHLC_CONTRACT_ALIASES)

        out = pd.DataFrame()
        out["timestamp"] = pd.to_datetime(df[datetime_col], errors="coerce")
        out["high"] = pd.to_numeric(df[high_col], errors="coerce")
        out["low"] = pd.to_numeric(df[low_col], errors="coerce")
        out["open"] = pd.to_numeric(df[open_col], errors="coerce") if open_col else None
        out["close"] = pd.to_numeric(df[close_col], errors="coerce") if close_col else None
        out["volume"] = pd.to_numeric(df[volume_col], errors="coerce") if volume_col else None

        if contract_col:
            out["contract"] = df[contract_col].astype(str).str.strip()
        elif sheet_name:
            out["contract"] = str(sheet_name).strip()
        elif fallback_contract:
            out["contract"] = fallback_contract.strip()
        else:
            raise ValueError(
                "OHLC file has no Contract column and this upload didn't specify a contract — "
                "provide one (e.g. 'GC Dec26')."
            )

        out = out.dropna(subset=["timestamp", "high", "low"])
        out = out[out["contract"] != ""]
        frames.append(out)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["contract", "timestamp", "open", "high", "low", "close", "volume"]
    )
    return combined


# ============================================================
# Progress broadcasting
# ============================================================

def _emit(loop: asyncio.AbstractEventLoop, job_id: str, **fields):
    message = {"type": "data_upload_progress", "job_id": job_id, **fields}
    try:
        asyncio.run_coroutine_threadsafe(ws.broadcast(message), loop)
    except Exception as e:
        logger.warning(f"Failed to broadcast upload progress: {e}")


# ============================================================
# Dedup + batched insert
# ============================================================

# Sentinel used only to make a NaN price compare equal to another NaN price
# during the anti-join merge below — an ordinary pandas/SQL merge treats
# NaN as never equal to NaN, which would make a T&S row with a genuinely
# missing price look "new" on every reupload even though it's already in
# the DB with the same missing price. Never written to the DB itself (see
# _none_if_nan below, applied separately when building insert records).
_PRICE_MERGE_SENTINEL = -1.0e18


def _upsert_time_and_sales(db, df: pd.DataFrame, job_id, loop, base_pct: float, pct_share: float) -> tuple[int, int]:
    if df.empty:
        _emit(loop, job_id, stage="time_and_sales", message="Time & Sales: no valid rows in file", percent=base_pct + pct_share)
        return 0, 0

    contracts = df["contract"].unique().tolist()
    existing_rows = (
        db.query(TimeAndSales.contract, TimeAndSales.timestamp, TimeAndSales.qty, TimeAndSales.price)
        .filter(TimeAndSales.contract.in_(contracts))
        .all()
    )

    df = df.drop_duplicates(subset=["contract", "timestamp", "qty", "price"]).reset_index(drop=True)
    total_parsed = len(df)

    if existing_rows:
        # A plain Series.isin() against a set of tuples silently mismatches
        # here — pandas hands the tuple collection to numpy, which (since
        # every tuple is the same length) reshapes it into a 2D array
        # instead of an array of tuple objects, breaking elementwise
        # comparison. A merge-based anti-join compares column-by-column
        # instead, which has no such pitfall.
        existing_df = pd.DataFrame(existing_rows, columns=["contract", "timestamp", "qty", "price"])
        df["_price_key"] = df["price"].fillna(_PRICE_MERGE_SENTINEL)
        existing_df["_price_key"] = existing_df["price"].fillna(_PRICE_MERGE_SENTINEL)
        merged = df.merge(
            existing_df[["contract", "timestamp", "qty", "_price_key"]],
            on=["contract", "timestamp", "qty", "_price_key"],
            how="left",
            indicator=True,
        )
        new_df = df[(merged["_merge"] == "left_only").values].drop(columns=["_price_key"])
    else:
        new_df = df

    total_new = len(new_df)
    skipped = total_parsed - total_new

    if total_new == 0:
        _emit(
            loop, job_id, stage="time_and_sales",
            message=f"Time & Sales: {skipped} row(s) already in database, nothing new to add",
            percent=base_pct + pct_share,
        )
        return 0, skipped

    records = [
        {"contract": row.contract, "timestamp": row.timestamp.to_pydatetime(), "qty": float(row.qty), "price": _none_if_nan(row.price)}
        for row in new_df.itertuples(index=False)
    ]

    inserted = 0
    for i in range(0, total_new, INSERT_BATCH_SIZE):
        chunk = records[i:i + INSERT_BATCH_SIZE]
        db.bulk_insert_mappings(TimeAndSales, chunk)
        db.commit()
        inserted += len(chunk)
        pct = base_pct + pct_share * (inserted / total_new)
        _emit(
            loop, job_id, stage="time_and_sales",
            message=f"Time & Sales: added {inserted}/{total_new} new row(s) ({skipped} already present)",
            percent=pct,
        )

    return inserted, skipped


def _upsert_ohlc_bars(db, df: pd.DataFrame, job_id, loop, base_pct: float, pct_share: float) -> tuple[int, int]:
    if df.empty:
        _emit(loop, job_id, stage="ohlc_bars", message="OHLC: no valid rows in file", percent=base_pct + pct_share)
        return 0, 0

    contracts = df["contract"].unique().tolist()
    existing_rows = (
        db.query(OhlcBar.contract, OhlcBar.timestamp)
        .filter(OhlcBar.contract.in_(contracts))
        .all()
    )

    df = df.drop_duplicates(subset=["contract", "timestamp"]).reset_index(drop=True)
    total_parsed = len(df)

    if existing_rows:
        # See the comment in _upsert_time_and_sales — merge-based anti-join,
        # not Series.isin() against a set of tuples.
        existing_df = pd.DataFrame(existing_rows, columns=["contract", "timestamp"])
        merged = df.merge(existing_df, on=["contract", "timestamp"], how="left", indicator=True)
        new_df = df[(merged["_merge"] == "left_only").values]
    else:
        new_df = df

    total_new = len(new_df)
    skipped = total_parsed - total_new

    if total_new == 0:
        _emit(
            loop, job_id, stage="ohlc_bars",
            message=f"OHLC: {skipped} bar(s) already in database, nothing new to add",
            percent=base_pct + pct_share,
        )
        return 0, skipped

    records = new_df.apply(
        lambda row: {
            "contract": row["contract"],
            "timestamp": row["timestamp"].to_pydatetime(),
            "open": _none_if_nan(row["open"]),
            "high": row["high"],
            "low": row["low"],
            "close": _none_if_nan(row["close"]),
            "volume": _none_if_nan(row["volume"]),
        },
        axis=1,
    ).tolist()

    inserted = 0
    for i in range(0, total_new, INSERT_BATCH_SIZE):
        chunk = records[i:i + INSERT_BATCH_SIZE]
        db.bulk_insert_mappings(OhlcBar, chunk)
        db.commit()
        inserted += len(chunk)
        pct = base_pct + pct_share * (inserted / total_new)
        _emit(
            loop, job_id, stage="ohlc_bars",
            message=f"OHLC: added {inserted}/{total_new} new bar(s) ({skipped} already present)",
            percent=pct,
        )

    return inserted, skipped


# ============================================================
# Background job
# ============================================================

def _process_upload_job(
    job_id: str,
    ts_bytes: Optional[bytes],
    ts_filename: Optional[str],
    ohlc_bytes: Optional[bytes],
    ohlc_filename: Optional[str],
    ohlc_contract: Optional[str],
    loop: asyncio.AbstractEventLoop,
):
    db = SessionLocal()
    summary = {}
    try:
        _emit(loop, job_id, stage="reading", message="Reading file(s)...", percent=1)

        ts_df = parse_time_sales(ts_bytes, ts_filename) if ts_bytes else None
        ohlc_df = parse_ohlc(ohlc_bytes, ohlc_filename, ohlc_contract) if ohlc_bytes else None

        sections = [
            (name, df, upsert_fn)
            for name, df, upsert_fn in [
                ("time_and_sales", ts_df, _upsert_time_and_sales),
                ("ohlc_bars", ohlc_df, _upsert_ohlc_bars),
            ]
            if df is not None
        ]

        base_pct = 5.0
        pct_share = 90.0 / len(sections) if sections else 0.0

        for name, df, upsert_fn in sections:
            inserted, skipped = upsert_fn(db, df, job_id, loop, base_pct, pct_share)
            summary[name] = {"parsed": len(df), "inserted": inserted, "skipped": skipped}
            base_pct += pct_share

        _emit(loop, job_id, stage="done", message="Upload complete", percent=100, done=True, summary=summary)
    except Exception as e:
        logger.exception(f"Data upload job {job_id} failed")
        _emit(loop, job_id, stage="error", message=str(e), percent=100, done=True, error=str(e))
    finally:
        db.close()


# ============================================================
# Route
# ============================================================

@router.post("/upload")
async def upload_data(
    time_sales_file: Optional[UploadFile] = File(None),
    ohlc_file: Optional[UploadFile] = File(None),
    ohlc_contract: Optional[str] = Form(None),
):
    """Kicks off parsing + DB dedup/insert in the background and returns
    immediately with a job_id — the frontend tracks progress via the
    'data_upload_progress' websocket messages this job broadcasts (see
    _process_upload_job/_emit), not via this response."""
    if not time_sales_file and not ohlc_file:
        raise HTTPException(status_code=400, detail="Provide at least one file (Time & Sales and/or OHLC).")

    ts_bytes = await time_sales_file.read() if time_sales_file else None
    ts_filename = time_sales_file.filename if time_sales_file else None
    ohlc_bytes = await ohlc_file.read() if ohlc_file else None
    ohlc_filename = ohlc_file.filename if ohlc_file else None

    job_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    asyncio.create_task(
        asyncio.to_thread(
            _process_upload_job, job_id, ts_bytes, ts_filename, ohlc_bytes, ohlc_filename, ohlc_contract, loop
        )
    )
    return {"job_id": job_id}
