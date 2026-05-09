#!/usr/bin/env python3
"""Prove ledger DB satisfies structural PRAGMAs + every CHECK/NOT NULL rule from ``full_schema.sql``.

Uses ``pipeline/ledger`` (constraint audit) — keep SQL in sync when DDL changes.

  PYTHONPATH=app/backend python app/backend/scripts/verify_ledger_integrity.py
  PYTHONPATH=app/backend python app/backend/scripts/verify_ledger_integrity.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import config
from pipeline.ledger import audit_ledger_constraints, format_report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=None, help="Path to ledger.sqlite (default: config.ledger_db_file)")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    args = p.parse_args()
    path = args.db or config.ledger_db_file

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        report = audit_ledger_constraints(conn)
    finally:
        conn.close()

    if args.json:
        out = {
            "integrity_check": report.integrity_check,
            "foreign_key_violation_count": report.foreign_key_violation_count,
            "expected_triggers_present": report.expected_triggers_present,
            "missing_triggers": list(report.missing_triggers),
            "ok": report.ok,
            "violations": [
                {
                    "table": v.table,
                    "rule_id": v.rule_id,
                    "description": v.description,
                    "count": v.count,
                    "sample_detail": v.sample_detail,
                }
                for v in report.violations
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
