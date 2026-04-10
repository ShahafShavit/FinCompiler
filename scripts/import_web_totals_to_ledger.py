#!/usr/bin/env python3
"""Import ``web/data/web_totals.csv`` into the SQLite ledger (MIG-D1 / MIG-D3).

Run from repo root (with venv if you use one)::

  PYTHONPATH=. python scripts/import_web_totals_to_ledger.py

Options::

  --csv PATH       Source file (default: config.web_totals_file)
  --db PATH        SQLite file (default: config.ledger_db_file)
  --verify-only    Only run parity checks (DB must already match CSV row count)
  --no-replace     Append without deleting ledger rows first (use with care)

Prefer a backup of ``data/`` before first production import.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import config
from pipeline.web_totals_import import (
    import_web_totals_to_ledger,
    load_web_totals_dataframe,
    verify_ledger_against_csv,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", default=None, help="Path to web_totals-style CSV")
    p.add_argument("--db", default=None, help="Path to ledger.sqlite")
    p.add_argument(
        "--verify-only",
        action="store_true",
        help="Load CSV and compare aggregates to DB without importing",
    )
    p.add_argument(
        "--no-replace",
        action="store_true",
        help="Do not clear ledger_transaction before insert",
    )
    p.add_argument("--json", action="store_true", help="Print report as JSON")
    args = p.parse_args()

    csv_path = args.csv or config.web_totals_file
    db_path = args.db or config.ledger_db_file

    if args.verify_only:
        df = load_web_totals_dataframe(csv_path)
        report = verify_ledger_against_csv(df, db_path)
    else:
        report = import_web_totals_to_ledger(
            csv_path,
            db_path,
            replace=not args.no_replace,
        )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("parity_ok:", report.get("parity_ok"))
        for k in (
            "rows_imported",
            "rows_without_fingerprint",
            "csv_rows",
            "db_rows",
            "sum_debit_csv",
            "sum_debit_db",
            "sum_credit_csv",
            "sum_credit_db",
            "error",
        ):
            if k in report:
                print(f"{k}: {report[k]}")
        mm = report.get("order_mismatches") or []
        if mm:
            print("order_mismatches (up to 30):")
            for m in mm:
                print(" ", m)

    return 0 if report.get("parity_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
