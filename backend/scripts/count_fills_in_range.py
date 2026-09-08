"""
Count how many fills TT itself reports for one account between a given
start/end time — fetched LIVE from the TT API, not our DB — so this
answers "does TT actually have these fills at all" independent of whether
our sync job ever pulled/stored them. Used for the GDU missing-trades
investigation (see missing_fills_vs_workbook.csv): if a trade is missing
here too, re-fetching into our own DB won't help, since there'd be nothing
for TT to give us.

Talks to TT directly — no DB involved at all, just the account's own TT
account ID (same approach as test_fills_retention.py). Edit the SETTINGS
block below, then run locally (loads TT_API_KEY/TT_API_SECRET/
TT_ENVIRONMENT from the repo-root .env, same as the app):

    uv run python scripts/count_fills_in_range.py

Or inside the prod container, which already has those set in its own env:

    docker exec mm-pnl-backend-1 python scripts/count_fills_in_range.py
"""
import csv
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from routes.TT_routes import TTClient, IST, ist_date_to_ns


# ============================================================
# SETTINGS
# ============================================================

TT_ACCOUNT_ID = 1327405  # R5041

# Both interpreted as IST (same convention as ist_date_to_ns elsewhere in
# this app) — 'YYYY-MM-DD' calendar dates.
START_DATE = "2026-09-01"
END_DATE = "2026-09-03"

# Optional substring filter on securityDesc — set to None to skip.
CONTRACT = "GDU"

# Optional path to also write the matched fills as a CSV (every raw field
# TT sent, unmodified, plus one added "datetime_ist" column) — set to None
# to only print the summary.
OUT = "raw_fills_test.csv"


MLRT_LABELS = {
    "1": "1 (outright/single-leg)",
    "2": "2 (per-leg of a multi-leg/spread)",
    "3": "3 (multi-leg/spread aggregate)",
}


def ns_to_ist(ns) -> str:
    dt = datetime.fromtimestamp(int(ns) / 1_000_000_000, tz=IST)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def write_csv(fills, path):
    """Raw fills, exactly as TT sent them (every field, untouched) — just
    with one added 'datetime_ist' column up front for readability."""
    fills_sorted = sorted(fills, key=lambda f: int(f.get("transactTime", "0")))
    fieldnames = ["datetime_ist"] + sorted({key for fill in fills_sorted for key in fill.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for fill in fills_sorted:
            writer.writerow({"datetime_ist": ns_to_ist(fill.get("transactTime")), **fill})
    print(f"Wrote {len(fills_sorted)} fill(s) to {path}")


def main():
    tt_client = TTClient(
        api_key=os.getenv("TT_API_KEY"),
        api_secret=os.getenv("TT_API_SECRET"),
        environment=os.getenv("TT_ENVIRONMENT"),
    )

    start_ns = ist_date_to_ns(START_DATE)
    end_ns = ist_date_to_ns(END_DATE, end_of_day=True)

    print(f"tt_account_id: {TT_ACCOUNT_ID}")
    print(f"Range: {START_DATE} -> {END_DATE} (IST, inclusive)")
    if CONTRACT:
        print(f"Contract filter: securityDesc contains {CONTRACT!r}")

    fills = tt_client.get_all_fills(start_ns, end_ns, accountID=TT_ACCOUNT_ID)

    # TT doesn't reliably honor minTimestamp/maxTimestamp server-side — same
    # client-side re-filter routes/fills.py does after every TT fetch.
    fills = [f for f in fills if start_ns <= int(f.get("transactTime", "0")) <= end_ns]

    if CONTRACT:
        fills = [f for f in fills if CONTRACT.upper() in (f.get("securityDesc") or "").upper()]

    print(f"\nTotal fills: {len(fills)}")

    if OUT:
        write_csv(fills, OUT)

    if not fills:
        return

    by_mlrt = Counter(f.get("multiLegReportingType") for f in fills)
    by_contract = Counter(f.get("securityDesc") for f in fills)

    print("\nBy multiLegReportingType:")
    for mlrt, count in sorted(by_mlrt.items(), key=lambda kv: (kv[0] is None, kv[0])):
        label = MLRT_LABELS.get(mlrt, f"{mlrt!r} (unrecognized)")
        print(f"  {label}: {count}")

    print("\nBy contract (securityDesc):")
    for contract, count in by_contract.most_common():
        print(f"  {contract}: {count}")


if __name__ == "__main__":
    main()
