# routes/calendars.py
"""
Serves CME's (and, eventually, other exchanges') "Quoting Calendar" HTML
exports — reference files dropped into backend/calendars/<EXCHANGE>/*.html —
as structured JSON for the frontend's Calendar page.

These are the exchange's *market-maker quoting obligation* calendars (which
days/contracts a program requires quoting for), NOT contract expiry / First
Notice Day data — TT's own instrument data already has expirationDate/
lastTradeDate per contract (see Product model) for that, and FND tracking is
deliberately out of scope here for now.

New exchanges: just add a new folder under backend/calendars/ with the same
HTML export format; no code change needed, folders are discovered at
request time.
"""
import os
import re
import logging
from datetime import datetime, date
from calendar import monthrange
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger(__name__)

CALENDARS_DIR = os.path.join(os.path.dirname(__file__), "..", "calendars")

_TITLE_RE = re.compile(r"<title>Quoting Calendar:\s*(.*?)</title>", re.S)
_HEADER_ROW_RE = re.compile(r"<tr>\s*<th>Date</th>(.*?)</tr>", re.S)
_TH_RE = re.compile(r"<th>(.*?)</th>", re.S)
_ROW_RE = re.compile(r"<tr[^>]*>\s*<td class=\"([^\"]*)\">(\d{4}-\d{2}-\d{2})</td>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_CELLHEADER_RE = re.compile(r"<span class=\"cellheader\">(.*?)</span>", re.S)
_CELLDETAIL_RE = re.compile(r"<span class=\"celldetail\">(.*?)</span>", re.S)

# path -> (mtime, parsed_result) — these files are dropped in manually and
# change rarely, so re-parsing only when a file's mtime changes (checked on
# every request, which is cheap) avoids both a stale cache and needless
# re-parsing of ~9+ multi-hundred-KB files on every page load.
_cache: Dict[str, tuple] = {}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_calendar_html(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    title_match = _TITLE_RE.search(html)
    program = title_match.group(1).strip() if title_match else os.path.splitext(os.path.basename(path))[0]

    header_row_match = _HEADER_ROW_RE.search(html)
    columns = [_clean(c) for c in _TH_RE.findall(header_row_match.group(1))] if header_row_match else []

    days = {}
    for status, date_str, rest in _ROW_RE.findall(html):
        cells = []
        for td_content in _TD_RE.findall(rest):
            header = _CELLHEADER_RE.search(td_content)
            details = [_clean(d) for d in _CELLDETAIL_RE.findall(td_content)]
            cells.append({"symbol": header.group(1).strip() if header else None, "details": details})
        days[date_str] = {"status": status, "cells": cells}

    return {"program": program, "columns": columns, "days": days}


def _get_parsed_calendar(path: str) -> dict:
    mtime = os.path.getmtime(path)
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    parsed = _parse_calendar_html(path)
    _cache[path] = (mtime, parsed)
    return parsed


def _list_exchanges() -> List[str]:
    if not os.path.isdir(CALENDARS_DIR):
        return []
    return sorted(
        name for name in os.listdir(CALENDARS_DIR)
        if os.path.isdir(os.path.join(CALENDARS_DIR, name))
    )


def _list_exchange_files(exchange: str) -> List[str]:
    exchange_dir = os.path.join(CALENDARS_DIR, exchange)
    if not os.path.isdir(exchange_dir):
        return []
    return sorted(
        os.path.join(exchange_dir, name)
        for name in os.listdir(exchange_dir)
        if name.lower().endswith(".html")
    )


@router.get("/exchanges")
def list_exchanges():
    """Exchange folders currently available under backend/calendars/."""
    return {"exchanges": _list_exchanges()}


@router.get("/overview")
def get_calendar_overview(exchange: str = "CME", month: Optional[str] = None):
    """
    All programs for an exchange, with their day data filtered to a single
    calendar month (YYYY-MM; defaults to the current month).
    """
    if month:
        try:
            year, mon = (int(part) for part in month.split("-"))
            datetime(year, mon, 1)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
    else:
        today = date.today()
        year, mon = today.year, today.month
        month = f"{year:04d}-{mon:02d}"

    days_in_month = monthrange(year, mon)[1]
    month_dates = [f"{year:04d}-{mon:02d}-{d:02d}" for d in range(1, days_in_month + 1)]

    files = _list_exchange_files(exchange)
    if not files:
        return {"exchange": exchange, "month": month, "programs": []}

    programs = []
    for path in files:
        try:
            parsed = _get_parsed_calendar(path)
        except Exception as e:
            logger.warning(f"Failed to parse calendar file {path}: {e}")
            continue

        programs.append({
            "program": parsed["program"],
            "columns": parsed["columns"],
            "days": {d: parsed["days"][d] for d in month_dates if d in parsed["days"]},
        })

    programs.sort(key=lambda p: p["program"])
    return {"exchange": exchange, "month": month, "programs": programs}
