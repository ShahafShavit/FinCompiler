"""Last-four to payer map. The name is joined at read time."""

from __future__ import annotations

from typing import Any

from ledger.store import connect_ledger, migrate_ledger_db


def list_card_payers(db_path: str) -> dict[str, Any]:
    migrate_ledger_db(db_path)
    conn = connect_ledger(db_path)
    try:
        mapped = [
            {"last_four": row[0], "payer": row[1]}
            for row in conn.execute(
                "SELECT last_four, payer FROM card_payer ORDER BY last_four"
            )
        ]
        known = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT TRIM("4 ספרות")
                FROM ledger_transaction
                WHERE TRIM(COALESCE("4 ספרות", '')) != ''
                ORDER BY 1
                """
            )
            if row[0]
        ]
    finally:
        conn.close()
    return {"ok": True, "rows": mapped, "known_last_fours": known}


def replace_card_payers(db_path: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    migrate_ledger_db(db_path)
    cleaned: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        last_four = str(row.get("last_four") or "").strip()
        payer = str(row.get("payer") or "").strip()
        if not last_four or not payer:
            continue
        cleaned.append((last_four, payer))
    conn = connect_ledger(db_path)
    try:
        conn.execute("DELETE FROM card_payer")
        conn.executemany(
            "INSERT INTO card_payer (last_four, payer) VALUES (?, ?)",
            cleaned,
        )
        conn.commit()
    finally:
        conn.close()
    return list_card_payers(db_path)


def payer_sql(last_four_expr: str = '"4 ספרות"') -> str:
    """Scalar subquery. ``last_four_expr`` is a SQL expression for the card digits."""
    return (
        "(SELECT payer FROM card_payer "
        f"WHERE card_payer.last_four = TRIM(COALESCE({last_four_expr}, '')))"
    )
