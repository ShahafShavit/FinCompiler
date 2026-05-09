"""
Infer ``statement_month`` for installment text (תשלום x מתוך y) on ``ledger_transaction``.

Used by :func:`run_installment_statement_month_fill` and ``scripts/fill_installment_statement_months.py``.
Updates **only** rows where ``statement_month`` IS NULL.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

import config
from pipeline.ledger import LEDGER_SQL_TX_INCLUDED, migrate_ledger_db

log = logging.getLogger(__name__)

_INSTALLMENT_RE = re.compile(r"תשלום\s*(\d+)\s*מתוך\s*(\d+)")

CSV_FIELDNAMES = [
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


@dataclass(frozen=True)
class Row:
    id: int
    taarich: str
    bh: float
    source: str | None
    detail: str
    x: int
    y: int


def default_preview_csv_path() -> str:
    """``data/export/installment_statement_month_preview.csv`` (beside ``compiled/``)."""
    export_dir = os.path.dirname(os.path.dirname(config.compiled_file))
    return os.path.normpath(os.path.join(export_dir, "installment_statement_month_preview.csv"))


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
        f"""
        SELECT id, "תאריך", "בחובה", "מקור עסקה", "פירוט נוסף", statement_month
        FROM ledger_transaction
        WHERE statement_month IS NULL
          AND {LEDGER_SQL_TX_INCLUDED}
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


def run_installment_statement_month_fill(
    db_path: str | None = None,
    *,
    dry_run: bool = False,
    amount_tol: float = 10.0,
    output_csv: str | None = None,
    sink: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Compute installment ``statement_month`` values and optionally apply (NULL rows only).

    Writes a preview CSV to ``output_csv`` or :func:`default_preview_csv_path`.
    """
    path = db_path if db_path is not None else config.ledger_db_file
    out_path = os.path.abspath(output_csv if output_csv is not None else default_preview_csv_path())

    if not os.path.isfile(path):
        log.warning("installment_statement_month: no ledger DB at %s (skip)", path)
        return {"ok": False, "skipped": True, "reason": "no_database_file"}

    migrate_ledger_db(path)

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    rows_updated = 0
    try:
        raw = _load_candidates(conn)
        proposed, warnings, cluster_stats = _build_updates(raw, amount_tol)

        for w in warnings:
            log.warning("%s", w)

        nf = cluster_stats["group_full"] + cluster_stats["group_partial"]
        if nf:
            msg = (
                f"Installment groups: {cluster_stats['group_full']} full, "
                f"{cluster_stats['group_partial']} partial "
                f"(see group_count_verification / group_indices_verification)"
            )
            log.info(msg)
            if sink:
                sink(msg)

        _parent = os.path.dirname(out_path)
        if _parent:
            os.makedirs(_parent, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            wr.writeheader()
            for row in proposed:
                wr.writerow({**row, "applied": "0"})

        log.info(
            "installment_statement_month: wrote %s row(s) preview to %s",
            len(proposed),
            out_path,
        )
        if sink:
            sink(f"INSTALLMENT STATEMENT MONTH: preview {len(proposed)} row(s) -> {out_path}")

        if dry_run:
            if sink:
                sink("INSTALLMENT STATEMENT MONTH: dry run (no DB UPDATE)")
            return {
                "ok": True,
                "dry_run": True,
                "rows_updated": 0,
                "proposed_count": len(proposed),
                "warnings": warnings,
                "cluster_stats": cluster_stats,
                "output_csv": out_path,
            }

        for row in proposed:
            cur = conn.execute(
                """
                UPDATE ledger_transaction
                SET statement_month = ?
                WHERE id = ? AND statement_month IS NULL
                """,
                (row["statement_month_new"], row["id"]),
            )
            rows_updated += cur.rowcount
        conn.commit()

        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            wr.writeheader()
            for row in proposed:
                wr.writerow({**row, "applied": "1"})

        log.info("installment_statement_month: applied %s UPDATE(s)", rows_updated)
        if sink:
            sink(f"INSTALLMENT STATEMENT MONTH: applied {rows_updated} UPDATE(s) (NULL rows only)")

        return {
            "ok": True,
            "dry_run": False,
            "rows_updated": rows_updated,
            "proposed_count": len(proposed),
            "warnings": warnings,
            "cluster_stats": cluster_stats,
            "output_csv": out_path,
        }
    finally:
        conn.close()
