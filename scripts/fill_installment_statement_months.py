#!/usr/bin/env python3
"""CLI for installment ``statement_month`` fill — logic lives in ``pipeline.installment_statement_months``.

  PYTHONPATH=. python scripts/fill_installment_statement_months.py
  PYTHONPATH=. python scripts/fill_installment_statement_months.py --apply
  PYTHONPATH=. python scripts/fill_installment_statement_months.py --db path/to/ledger.sqlite -o preview.csv
"""

from __future__ import annotations

import argparse
import os
import sys

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import config
from pipeline.installment_statement_months import run_installment_statement_month_fill


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=None, help="ledger.sqlite path (default: config.ledger_db_file)")
    p.add_argument(
        "-o",
        "--output",
        default="installment_statement_month_preview.csv",
        help="CSV path (default: ./installment_statement_month_preview.csv)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write statement_month to the database (NULL rows only).",
    )
    p.add_argument(
        "--amount-tol",
        type=float,
        default=10.0,
        help="Max (max-min) בחובה within a cluster (default: 10).",
    )
    args = p.parse_args()

    db_path = args.db or config.ledger_db_file
    if not os.path.isfile(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    out = os.path.abspath(args.output)
    res = run_installment_statement_month_fill(
        db_path,
        dry_run=not args.apply,
        amount_tol=args.amount_tol,
        output_csv=out,
    )
    if not res.get("ok"):
        return 1
    for w in res.get("warnings") or []:
        print(w, file=sys.stderr)
    if args.apply:
        print(f"Applied {res.get('rows_updated', 0)} UPDATE(s). Preview: {res.get('output_csv')}")
    else:
        print(f"Dry run: no database changes. Preview: {res.get('output_csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
