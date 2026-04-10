#!/usr/bin/env python3
"""Import static CSVs into the ledger (MIG-D2).

- ``data/static/stores_to_categories.csv`` → ``store`` / ``store_category``
- ``data/static/similar_pairs.csv`` → ``similar_category_pair``

Run from repo root::

  PYTHONPATH=. python scripts/import_stores_to_ledger.py

Options::

  --csv           Stores CSV (default: config.stores_to_categories_file)
  --similar-csv   Similar pairs CSV (default: config.similar_categories_file)
  --db            SQLite file (default: config.ledger_db_file)
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
from pipeline.static_store_import import import_stores_to_ledger


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", default=None, help="Path to stores_to_categories CSV")
    p.add_argument(
        "--similar-csv",
        default=None,
        dest="similar_csv",
        help="Path to similar_pairs CSV",
    )
    p.add_argument("--db", default=None, help="Path to ledger.sqlite")
    p.add_argument("--json", action="store_true", help="Print report as JSON")
    args = p.parse_args()

    report = import_stores_to_ledger(
        args.csv or config.stores_to_categories_file,
        args.db or config.ledger_db_file,
        similar_pairs_csv=args.similar_csv,
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for k in (
            "ok",
            "stores_inserted",
            "store_category_rows_inserted",
            "similar_pair_rows_inserted",
            "stores_forced_dynamic",
            "csv_path",
            "similar_pairs_csv",
            "db_path",
        ):
            if k in report:
                print(f"{k}: {report[k]}")
        for w in report.get("warnings") or []:
            print("warning:", w)

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
