#!/usr/bin/env python3
"""Import one or more wide ``holdings.csv`` files into ``holdings_balance``.

Example::

  PYTHONPATH=. python scripts/import_holdings_to_ledger.py \\
    data/export/compiled/2024/holdings.csv \\
    data/export/compiled/2025/holdings.csv
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
from pipeline.holdings_csv_import import import_holdings_csvs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "paths",
        nargs="+",
        help="Wide holdings CSV paths (e.g. per-year under data/export/compiled/)",
    )
    p.add_argument("--db", default=None, help="Ledger SQLite path (default: config.ledger_db_file)")
    p.add_argument(
        "--clear-holdings-first",
        action="store_true",
        help="Delete all rows in holdings_balance before import",
    )
    p.add_argument("--json", action="store_true", help="Print report as JSON")
    args = p.parse_args()

    abs_paths = [os.path.abspath(x) for x in args.paths]
    for x in abs_paths:
        if not os.path.isfile(x):
            print(f"Not found: {x}", file=sys.stderr)
            return 2

    report = import_holdings_csvs(
        abs_paths,
        args.db or config.ledger_db_file,
        clear_holdings_first=args.clear_holdings_first,
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("parity_ok:", report.get("parity_ok"))
        for k in (
            "rows_upserted",
            "holdings_table_count",
            "expected_logical_rows",
            "source_files",
        ):
            if k in report:
                print(f"{k}: {report[k]}")
        if report.get("missing_keys"):
            print("missing_keys:", report["missing_keys"])
        if report.get("amount_mismatches"):
            print("amount_mismatches:", report["amount_mismatches"])

    return 0 if report.get("parity_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
