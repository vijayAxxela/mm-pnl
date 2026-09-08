"""
One-off backfill for fills.row_hash on rows that predate it being populated.

Why this exists: _is_duplicate_fill (routes/fills.py) requires BOTH exec_id
AND row_hash to match an existing row before skipping a fetched fill as a
duplicate. A row with row_hash = NULL can never satisfy that AND, so any
resync touching one of these rows' date range re-attempts to insert it and
fails on a unique constraint (exec_id, or record_id) instead of being
cleanly skipped. Every affected row already has its full raw TT payload
stored in `raw`, so row_hash is fully computable after the fact — same
_compute_row_hash function fills.py uses for every new fill.

Safe to re-run — only ever touches rows where row_hash IS NULL, and setting
the same hash twice is a no-op.

    uv run python scripts/backfill_fills_row_hash.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import SessionLocal, Fill
from routes.fills import _compute_row_hash

BATCH_SIZE = 500


def main():
    db = SessionLocal()
    try:
        total = db.query(Fill).filter(Fill.row_hash.is_(None)).count()
        if not total:
            print("No fills with a NULL row_hash — nothing to backfill.")
            return

        print(f"Backfilling row_hash for {total} fill(s)...")
        updated = 0
        # Batched, not one big query.all() — 4700+ rows, each with its
        # sizeable raw JSON payload, isn't worth holding in memory at once.
        while True:
            batch = db.query(Fill).filter(Fill.row_hash.is_(None)).limit(BATCH_SIZE).all()
            if not batch:
                break
            for f in batch:
                f.row_hash = _compute_row_hash(f.raw)
            db.commit()
            updated += len(batch)
            print(f"  {updated}/{total}")

        print(f"\nDone — backfilled row_hash for {updated} fill(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
