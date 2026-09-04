"""
Empirical check of TT's fills-history retention window, using real live
credentials against real data — rather than trusting docs/web search, which
disagreed with itself on whether it's ~90 days.

Fetches CUMULATIVE ranges (N days ago -> today) for N = 30, 60, 90, 120 —
each range fully contains the previous one, so if the fill count stops
growing between two offsets (e.g. 90 days gives the same count as 120),
that's the retention cutoff: TT isn't returning anything further back
regardless of how much earlier the range asks for.

Talks to TT directly — no DB involved at all, just the account's own TT
account ID. Set TT_ACCOUNT_ID below, then run locally (loads TT_API_KEY/
TT_API_SECRET/TT_ENVIRONMENT from the repo-root .env itself, same as the
app):

    uv run python scripts/test_fills_retention.py

Or inside the prod container, which already has those set in its own env:

    docker exec mm-pnl-backend-1 python scripts/test_fills_retention.py
"""
import sys
import os
import csv
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from routes.TT_routes import TTClient, IST, ist_date_to_ns

OFFSETS_DAYS = [30, 60, 90, 120]
TT_ACCOUNT_ID = 1347303  # LGBEE838


def write_csv(fills, path):
    if not fills:
        return
    fieldnames = sorted({key for fill in fills for key in fill.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(fills)


def main():
    tt_client = TTClient(
        api_key=os.getenv("TT_API_KEY"),
        api_secret=os.getenv("TT_API_SECRET"),
        environment=os.getenv("TT_ENVIRONMENT"),
    )

    today = datetime.now(IST).date()
    end_ns = ist_date_to_ns(today.strftime("%Y-%m-%d"), end_of_day=True)
    print(f"tt_account_id: {TT_ACCOUNT_ID}")
    print(f"Today (IST): {today}\n")

    prev_count = None
    for offset in OFFSETS_DAYS:
        start_date = today - timedelta(days=offset)
        start_str = start_date.strftime("%Y-%m-%d")
        start_ns = ist_date_to_ns(start_str)

        try:
            fills = tt_client.get_all_fills(start_ns, end_ns, accountID=TT_ACCOUNT_ID)
            count = len(fills)
            csv_path = f"test_fills/fills_{TT_ACCOUNT_ID}_last{offset}d.csv"
            write_csv(fills, csv_path)

            plateau = " <- SAME as previous offset, likely hit the retention cutoff" if count == prev_count else ""
            print(f"  {start_str} to {today} ({offset:>3} days): {count} fill(s) -> {csv_path}{plateau}")
            prev_count = count
        except Exception as e:
            print(f"  {start_str} to {today} ({offset:>3} days): ERROR — {e}")

    print(
        "\nEach range fully contains the previous one, so the count should only "
        "ever grow (or stay the same right at the edges, e.g. no new trades "
        "happened to fall in the extra days). If it stops growing entirely at "
        "some offset and stays flat from there on, that offset is the retention "
        "cutoff."
    )


if __name__ == "__main__":
    main()
