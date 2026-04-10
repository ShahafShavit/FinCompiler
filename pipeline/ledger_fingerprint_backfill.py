"""
Set ``ledger_transaction.fingerprint`` for rows where it is NULL using
:func:`pipeline.csv_handler.generate_transaction_fingerprint`.

The algorithm uses the same fields as compile (``תאריך``, ``בחובה``, ``בזכות``, ``מקור עסקה``,
``פירוט נוסף``, ``תאור מורחב``) — all present on ``ledger_transaction``. Rows that still
cannot be fingerprinted (bad date / type errors) are left NULL. If the computed fingerprint
already exists on another row, the update is skipped to satisfy ``UNIQUE(fingerprint)``.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

import pandas as pd

from pipeline.csv_handler import generate_transaction_fingerprint

log = logging.getLogger(__name__)

_ROW_KEYS = (
    "תאריך",
    "בחובה",
    "בזכות",
    "מקור עסקה",
    "פירוט נוסף",
    "תאור מורחב",
)


@dataclass
class BackfillStats:
    examined: int = 0
    updated: int = 0
    skipped_uncomputable: int = 0  # algorithm returned None / empty
    skipped_would_duplicate: int = 0  # UNIQUE(fingerprint) would be violated


@dataclass
class WouldDuplicateDetail:
    """A NULL row skipped because ``computed_fingerprint`` is already taken."""

    id: int
    computed_fingerprint: str
    conflicts_with_id: int
    """Other ``ledger_transaction.id`` that already owns this fingerprint (ledger or earlier NULL in batch)."""

    conflict_kind: str
    """``ledger_row`` or ``batch_earlier_null``."""

    תאריך: str | None
    בחובה: float
    בזכות: float
    makor: str | None
    """``מקור עסקה`` at time of report."""


def _row_series_from_sqlite(row: sqlite3.Row) -> pd.Series:
    d = {k: row[k] for k in _ROW_KEYS}
    return pd.Series(d)


def list_would_duplicate_null_rows(
    db_path: str,
) -> tuple[BackfillStats, list[WouldDuplicateDetail]]:
    """
    Return stats (same as dry-run backfill) plus one record per NULL row skipped as duplicate.
    """
    return _simulate_null_fingerprints(db_path, dry_run=True, collect_duplicates=True)


def _simulate_null_fingerprints(
    db_path: str,
    *,
    dry_run: bool,
    collect_duplicates: bool,
) -> tuple[BackfillStats, list[WouldDuplicateDetail]]:
    stats = BackfillStats()
    duplicates: list[WouldDuplicateDetail] = []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ledger_owner: dict[str, int] = {}
        for r in conn.execute(
            "SELECT id, fingerprint FROM ledger_transaction WHERE fingerprint IS NOT NULL"
        ):
            fp = str(r["fingerprint"]).strip()
            if fp:
                ledger_owner[fp] = int(r["id"])

        batch_owner: dict[str, int] = {}

        cur = conn.execute(
            f"""
            SELECT id, {", ".join(f'"{k}"' for k in _ROW_KEYS)}
            FROM ledger_transaction
            WHERE fingerprint IS NULL
            ORDER BY id
            """
        )
        rows = cur.fetchall()
        stats.examined = len(rows)

        for row in rows:
            rid = int(row["id"])
            s = _row_series_from_sqlite(row)
            fp = generate_transaction_fingerprint(s)
            if fp is None or not str(fp).strip():
                stats.skipped_uncomputable += 1
                continue
            fp = str(fp).strip()

            if fp in ledger_owner:
                stats.skipped_would_duplicate += 1
                if collect_duplicates:
                    duplicates.append(
                        WouldDuplicateDetail(
                            id=rid,
                            computed_fingerprint=fp,
                            conflicts_with_id=ledger_owner[fp],
                            conflict_kind="ledger_row",
                            תאריך=row["תאריך"],
                            בחובה=float(row["בחובה"] or 0),
                            בזכות=float(row["בזכות"] or 0),
                            makor=row["מקור עסקה"],
                        )
                    )
                continue
            if fp in batch_owner:
                stats.skipped_would_duplicate += 1
                if collect_duplicates:
                    duplicates.append(
                        WouldDuplicateDetail(
                            id=rid,
                            computed_fingerprint=fp,
                            conflicts_with_id=batch_owner[fp],
                            conflict_kind="batch_earlier_null",
                            תאריך=row["תאריך"],
                            בחובה=float(row["בחובה"] or 0),
                            בזכות=float(row["בזכות"] or 0),
                            makor=row["מקור עסקה"],
                        )
                    )
                continue

            batch_owner[fp] = rid
            stats.updated += 1
            if not dry_run:
                conn.execute(
                    'UPDATE ledger_transaction SET fingerprint = ? WHERE id = ? AND fingerprint IS NULL',
                    (fp, rid),
                )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return stats, duplicates


def backfill_null_fingerprints(db_path: str, *, dry_run: bool = True) -> BackfillStats:
    """
    For each row with ``fingerprint IS NULL``, compute the fingerprint and UPDATE when unique.

    ``dry_run`` when True does not write.
    """
    stats, _ = _simulate_null_fingerprints(db_path, dry_run=dry_run, collect_duplicates=False)
    return stats
