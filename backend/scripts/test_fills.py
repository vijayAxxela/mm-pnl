import csv
import sys
import os
from collections import Counter
from datetime import datetime

import pytz
from sqlalchemy import BigInteger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import SessionLocal, Fill



START = "2026-09-01 00:00:00"
END = "2026-09-03 06:00:00"

CONTRACT = "GDU"       # matches Fill.security_desc, e.g. "GDU", "GDUZ6"
ACCOUNT = "R5041"      # matches Fill.account_name

OUT = "test_fills/test_fills.csv"


IST = pytz.timezone("Asia/Kolkata")

MLRT_LABELS = {
    "1": "1 (outright/single-leg)",
    "2": "2 (per-leg of a multi-leg/spread)",
    "3": "3 (multi-leg/spread aggregate)",
}


def ns_to_ist(ns: str) -> str:
    dt = datetime.fromtimestamp(int(ns) / 1_000_000_000, tz=IST)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def parse_ist(value: str) -> int:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Unrecognized datetime: {value!r}")
    dt = IST.localize(dt)
    return int(dt.timestamp() * 1_000_000_000)


def main():
    start_ns = parse_ist(START)
    end_ns = parse_ist(END)
    if end_ns <= start_ns:
        raise SystemExit("END must be after START")

    db = SessionLocal()
    try:
        query = db.query(Fill).filter(
            Fill.transact_time.cast(BigInteger) >= start_ns,
            Fill.transact_time.cast(BigInteger) < end_ns,
        )
        if CONTRACT:
            query = query.filter(Fill.security_desc.ilike(f"%{CONTRACT}%"))
        if ACCOUNT:
            query = query.filter(Fill.account_name.ilike(f"%{ACCOUNT}%"))

        fills = query.all()

        print(f"Range: {START} -> {END} (IST)")
        if CONTRACT:
            print(f"Contract filter: security_desc contains {CONTRACT!r}")
        if ACCOUNT:
            print(f"Account filter: account_name contains {ACCOUNT!r}")
        print(f"\nTotal fills: {len(fills)}")

        if OUT:
            fills_sorted = sorted(fills, key=lambda f: int(f.transact_time))
            with open(OUT, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "datetime_ist", "trade_date", "account_name", "security_desc",
                    "side", "qty", "price", "multi_leg_reporting_type",
                    "exec_id", "order_id", "transact_time_ns",
                ])
                for row in fills_sorted:
                    writer.writerow([
                        ns_to_ist(row.transact_time),
                        row.trade_date,
                        row.account_name,
                        row.security_desc,
                        row.side,
                        row.last_qty,
                        row.last_px,
                        row.multi_leg_reporting_type,
                        row.exec_id,
                        row.order_id,
                        row.transact_time,
                    ])
            print(f"Wrote {len(fills_sorted)} fill(s) to {OUT}")

        if not fills:
            return

        by_mlrt = Counter(f.multi_leg_reporting_type for f in fills)
        by_contract = Counter(f.security_desc for f in fills)
        by_account = Counter(f.account_name for f in fills)

        print("\nBy multiLegReportingType:")
        for mlrt, count in sorted(by_mlrt.items(), key=lambda kv: (kv[0] is None, kv[0])):
            label = MLRT_LABELS.get(mlrt, f"{mlrt!r} (unrecognized)")
            print(f"  {label}: {count}")

        print("\nBy contract (security_desc):")
        for contract, count in by_contract.most_common():
            print(f"  {contract}: {count}")

        print("\nBy account:")
        for account, count in by_account.most_common():
            print(f"  {account}: {count}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
