#!/usr/bin/env python3
"""Fill NULL ``ledger_transaction.fingerprint`` values using ``generate_transaction_fingerprint``.

Uses the same logic as compile / ``TransactionFile`` (see ``pipeline/csv_handler.py``). Safe to
re-run: only rows with ``fingerprint IS NULL`` are considered. Collisions with existing fingerprints
are skipped (see ``UNIQUE(fingerprint)`` in ``schema/ledger/full_schema.sql``).

  PYTHONPATH=. python scripts/backfill_ledger_null_fingerprints.py --dry-run
  PYTHONPATH=. python scripts/backfill_ledger_null_fingerprints.py
  PYTHONPATH=. python scripts/backfill_ledger_null_fingerprints.py --db path/to/ledger.sqlite

Back up the database before applying (see ``pipeline/backup.py`` / web control backup).
"""

from __future__ import annotations

import argparse
import os
import sys

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import config
from pipeline.ledger_fingerprint_backfill import (
    backfill_null_fingerprints,
    list_would_duplicate_null_rows,
)
from pipeline.ledger_migrate import migrate_ledger_db


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=None, help="Ledger DB path (default: config.ledger_db_file)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute stats only; do not write (default when neither --apply nor --dry-run)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Perform UPDATEs (mutually exclusive with dry-run for clarity)",
    )
    p.add_argument(
        "--show-would-duplicate",
        action="store_true",
        help="Print TSV lines for each NULL row skipped due to UNIQUE(fingerprint) (implies dry-run stats)",
    )
    args = p.parse_args()
    path = args.db or config.ledger_db_file
    dry = args.dry_run or not args.apply
    if args.dry_run and args.apply:
        print("Use either --dry-run or --apply, not both.", file=sys.stderr)
        return 2

    migrate_ledger_db(path)
    if args.show_would_duplicate:
        stats, dups = list_would_duplicate_null_rows(path)
        print(
            "id\tcomputed_fingerprint\tconflicts_with_id\tconflict_kind\t"
            "תאריך\tבחובה\tבזכות\tמקור עסקה"
        )
        for d in dups:
            mk = d.makor if d.makor is not None else ""
            print(
                f"{d.id}\t{d.computed_fingerprint}\t{d.conflicts_with_id}\t{d.conflict_kind}\t"
                f"{d.תאריך or ''}\t{d.בחובה}\t{d.בזכות}\t{mk}"
            )
    else:
        stats = backfill_null_fingerprints(path, dry_run=dry)
    print(
        f"examined={stats.examined} would_update={stats.updated} "
        f"skipped_uncomputable={stats.skipped_uncomputable} "
        f"skipped_would_duplicate={stats.skipped_would_duplicate} dry_run={dry}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
