#!/usr/bin/env python3
"""Fill ``statement_month`` for installment rows (תשלום x מתוך y) where it is NULL.

Rules (agreed spec):
- Parse x,y from ``פירוט נוסף``; skip rows whose detail contains **פרעון תשלום** (manual / out of scope).
- Group series: same ``מקור עסקה``, same ``תאריך``, same y, and ``בחובה`` within a cluster of
  diameter ≤ 10 (see clustering below).
- Calendar anchor: month **after** ``תאריך`` is the notional month for installment **x=1**.
- Row with installment **x**: ``statement_month`` = that anchor + **(x − 1)** months — even when **x=1** is
  not in this export (e.g. already filled elsewhere); clusters may start at any **x**.
- Each output row includes **group verification**: whether the cluster has as many entries as **y**
  (``group_count_verification``) and whether indices **1 … y** are all present
  (``group_indices_verification``); ``group_verification`` is **full** only when both match.
- Never UPDATE a row that already has a non-NULL ``statement_month``.

Default: **dry run** — writes a CSV of proposed values and does not modify the DB.
Pass ``--apply`` to perform UPDATEs (still only where ``statement_month`` IS NULL).

  PYTHONPATH=. python scripts/fill_installment_statement_months.py
  PYTHONPATH=. python scripts/fill_installment_statement_months.py --apply
  PYTHONPATH=. python scripts/fill_installment_statement_months.py --db path/to/ledger.sqlite -o preview.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import config

# Matches "תשלום 1 מתוך 5", "קרדיט - תשלום 1  מתוך 5", extra spaces OK.
_INSTALLMENT_RE = re.compile(r"תשלום\s*(\d+)\s*מתוך\s*(\d+)")


@dataclass(frozen=True)
class Row:
    id: int
    taarich: str
    bh: float
    source: str | None
    detail: str
    x: int
    y: int


def _parse_installment(detail: str) -> tuple[int, int] | None:
    if not detail or not detail.strip():
        return None
    m = _INSTALLMENT_RE.search(detail)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _exclude_parion(detail: str) -> bool:
    return "פרעון תשלום" in detail


def _month_after_taarich(taarich: str) -> str | None:
    """First installment month: month following the calendar month of ``תאריך`` (YYYY-MM)."""
    try:
        d = date.fromisoformat(taarich.strip()[:10])
    except ValueError:
        return None
    y, m = d.year, d.month
    if m == 12:
        return f"{y + 1}-01"
    return f"{y:04d}-{m + 1:02d}"


def _add_months(ym: str, n: int) -> str:
    y, mo = map(int, ym.split("-"))
    idx = y * 12 + (mo - 1) + n
    y2, r = divmod(idx, 12)
    return f"{y2:04d}-{r + 1:02d}"


def _cluster_by_amount(rows: list[Row], max_span: float) -> list[list[Row]]:
    """Partition ``rows`` (same y) by sorted ``בחובה`` into contiguous runs with max-min ≤ max_span."""
    if not rows:
        return []
    s = sorted(rows, key=lambda r: r.bh)
    out: list[list[Row]] = []
    i = 0
    n = len(s)
    while i < n:
        j = i
        while j + 1 < n and s[j + 1].bh - s[i].bh <= max_span:
            j += 1
        out.append(s[i : j + 1])
        i = j + 1
    return out


def _load_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT id, "תאריך", "בחובה", "מקור עסקה", "פירוט נוסף", statement_month
        FROM ledger_transaction
        WHERE statement_month IS NULL
          AND "תאריך" IS NOT NULL
          AND TRIM("תאריך") != ''
          AND "פירוט נוסף" IS NOT NULL
          AND TRIM("פירוט נוסף") != ''
        """
    )
    return [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]


def _build_updates(
    rows_raw: list[dict[str, Any]], amount_tol: float
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    """Returns (proposed_changes, skip_messages, cluster_counts: full vs partial ``group_verification``)."""
    proposed: list[dict[str, Any]] = []
    warnings: list[str] = []
    cluster_stats = {"group_full": 0, "group_partial": 0}

    parsed: list[Row] = []
    for r in rows_raw:
        did = r["id"]
        detail = str(r["פירוט נוסף"])
        if _exclude_parion(detail):
            continue
        inst = _parse_installment(detail)
        if not inst:
            continue
        x, y = inst
        if y < 1 or x < 1 or x > y:
            warnings.append(f"id={did} skip: bad installment x={x} y={y}")
            continue
        ta = str(r["תאריך"]).strip()[:10]
        try:
            bh = float(r["בחובה"])
        except (TypeError, ValueError):
            warnings.append(f"id={did} skip: bad בחובה")
            continue
        src = r["מקור עסקה"]
        if isinstance(src, str):
            src = src.strip() or None
        parsed.append(Row(id=did, taarich=ta, bh=bh, source=src, detail=detail, x=x, y=y))

    buckets: dict[tuple[str | None, str, int], list[Row]] = {}
    for row in parsed:
        key = (row.source, row.taarich, row.y)
        buckets.setdefault(key, []).append(row)

    for key, bucket in buckets.items():
        src, ta, y = key
        base = _month_after_taarich(ta)
        if base is None:
            warnings.append(f"bucket source={src!r} taarich={ta} y={y}: invalid תאריך")
            continue

        for cluster in _cluster_by_amount(bucket, amount_tol):
            by_x: dict[int, Row] = {}
            dup = False
            for row in cluster:
                if row.x in by_x:
                    warnings.append(
                        f"duplicate x={row.x} in cluster (ids {by_x[row.x].id}, {row.id}); skipping cluster"
                    )
                    dup = True
                    break
                by_x[row.x] = row
            if dup:
                continue

            x_min = min(by_x.keys())
            entry_count = len(cluster)
            count_full = entry_count == y
            indices_full = set(by_x.keys()) == set(range(1, y + 1))
            gv_count = "full" if count_full else "partial"
            gv_idx = "full" if indices_full else "partial"
            gv = "full" if (count_full and indices_full) else "partial"
            if gv == "full":
                cluster_stats["group_full"] += 1
            else:
                cluster_stats["group_partial"] += 1

            for row in cluster:
                sm = _add_months(base, row.x - 1)
                proposed.append(
                    {
                        "id": row.id,
                        "מקור עסקה": row.source or "",
                        "תאריך": row.taarich,
                        "בחובה": row.bh,
                        "פירוט נוסף": row.detail,
                        "installment_x": row.x,
                        "installment_y": row.y,
                        "group_min_installment_x": x_min,
                        "group_has_installment_1": "yes" if 1 in by_x else "no",
                        "group_entry_count": entry_count,
                        "group_expected_installments": y,
                        "group_count_verification": gv_count,
                        "group_indices_verification": gv_idx,
                        "group_verification": gv,
                        "statement_month_new": sm,
                        "statement_month_old": "",
                    }
                )

    proposed.sort(key=lambda d: (d["תאריך"] or "", d["מקור עסקה"] or "", d["installment_x"]))
    return proposed, warnings, cluster_stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="ledger.sqlite path (default: config.ledger_db_file)")
    p.add_argument(
        "-o",
        "--output",
        default="installment_statement_month_preview.csv",
        help="CSV path for proposed/applied rows (default: ./installment_statement_month_preview.csv)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write statement_month to the database (still only NULL rows).",
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

    try:
        from pipeline.ledger import migrate_ledger_db
    except ImportError as e:
        print(f"Cannot import pipeline.ledger: {e}", file=sys.stderr)
        return 1

    try:
        migrate_ledger_db(db_path)
    except Exception as e:
        print(f"migrate_ledger_db: {e}", file=sys.stderr)
        return 1

    fieldnames = [
        "id",
        "מקור עסקה",
        "תאריך",
        "בחובה",
        "פירוט נוסף",
        "installment_x",
        "installment_y",
        "group_min_installment_x",
        "group_has_installment_1",
        "group_entry_count",
        "group_expected_installments",
        "group_count_verification",
        "group_indices_verification",
        "group_verification",
        "statement_month_old",
        "statement_month_new",
        "applied",
    ]
    out_path = os.path.abspath(args.output)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        raw = _load_candidates(conn)
        proposed, warnings, cluster_stats = _build_updates(raw, args.amount_tol)

        for w in warnings:
            print(w, file=sys.stderr)

        nf = cluster_stats["group_full"] + cluster_stats["group_partial"]
        if nf:
            print(
                f"Installment groups: {cluster_stats['group_full']} full, "
                f"{cluster_stats['group_partial']} partial "
                f"(see group_count_verification / group_indices_verification)",
                file=sys.stderr,
            )

        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            wr.writeheader()
            for row in proposed:
                rec = {**row, "applied": "0"}
                wr.writerow(rec)

        print(f"Wrote {len(proposed)} row(s) to {out_path}")

        if not args.apply:
            print("Dry run: no database changes. Pass --apply to UPDATE.")
            return 0

        applied = 0
        for row in proposed:
            cur = conn.execute(
                """
                UPDATE ledger_transaction
                SET statement_month = ?
                WHERE id = ? AND statement_month IS NULL
                """,
                (row["statement_month_new"], row["id"]),
            )
            applied += cur.rowcount
        conn.commit()

        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            wr.writeheader()
            for row in proposed:
                rec = {**row, "applied": "1"}
                wr.writerow(rec)

        print(f"Applied {applied} UPDATE(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
