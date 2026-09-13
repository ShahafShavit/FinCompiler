"""Physical cash wallet: agorot helpers, denomination pad, cash_event CRUD.

Isolated from ``ledger_transaction`` — does not affect heatmap, dashboard cashflow,
or net worth.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Any

# Circulating ILS notes/coins, smallest unit = 10 agorot.
# Values are agorot (₪1 = 100). Display/pad order: large → small.
DENOMINATION_AGOROT: tuple[int, ...] = (
    20000,  # ₪200
    10000,  # ₪100
    5000,  # ₪50
    2000,  # ₪20
    1000,  # ₪10
    500,  # ₪5
    200,  # ₪2
    100,  # ₪1
    50,  # ₪0.50
    10,  # ₪0.10
)

# Excel backfill column order (small → large), matching the user's sheet.
EXCEL_DENOM_ORDER_AGOROT: tuple[int, ...] = (
    10,
    50,
    100,
    200,
    500,
    1000,
    2000,
    5000,
    10000,
    20000,
)

DENOMINATION_SET = frozenset(DENOMINATION_AGOROT)

# Paper notes vs coins (agorot). ₪10 is a coin (not a note).
PAPER_DENOMINATION_AGOROT: tuple[int, ...] = (20000, 10000, 5000, 2000)  # ₪200…₪20
COIN_DENOMINATION_AGOROT: tuple[int, ...] = (1000, 500, 200, 100, 50, 10)  # ₪10…₪0.10
PAPER_SET = frozenset(PAPER_DENOMINATION_AGOROT)
COIN_SET = frozenset(COIN_DENOMINATION_AGOROT)

CASH_EVENT_V21_DDL = """
CREATE TABLE IF NOT EXISTS cash_event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    event_date      TEXT NOT NULL CHECK (date(event_date) = event_date),
    kind            TEXT NOT NULL CHECK (kind IN ('spend', 'receive', 'count')),
    delta_agorot    INTEGER NOT NULL,
    counted_agorot  INTEGER,
    expected_agorot INTEGER,
    denominations   TEXT,
    note            TEXT,
    category        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        CHECK (datetime(created_at) IS NOT NULL)
) STRICT;
CREATE INDEX IF NOT EXISTS idx_cash_event_date ON cash_event (event_date, id);
"""

CASH_EVENT_V22_CATEGORY_DDL = """
ALTER TABLE cash_event ADD COLUMN category TEXT;
"""

_DATE_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class CashError(ValueError):
    """Domain validation error for cash wallet operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def ensure_cash_event_category_column(conn: sqlite3.Connection) -> None:
    """v22: add ``category`` if missing (idempotent)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cash_event'"
    ).fetchone()
    if not exists:
        conn.executescript(CASH_EVENT_V21_DDL)
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(cash_event)").fetchall()}
    if "category" not in cols:
        conn.execute("ALTER TABLE cash_event ADD COLUMN category TEXT")


def ensure_multiple_of_10_agorot(amount: int) -> None:
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise CashError("invalid_agorot", "amount must be an integer number of agorot")
    if amount % 10 != 0:
        raise CashError(
            "invalid_agorot",
            "amount must be a multiple of 10 agorot (₪0.10)",
        )


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def denominations_total_agorot(pieces: dict[str, Any] | dict[int, Any]) -> int:
    """Sum piece counts × denomination. Keys may be str or int agora values."""
    if not isinstance(pieces, dict):
        raise CashError("invalid_denominations", "denominations must be an object")
    total = 0
    for raw_key, raw_count in pieces.items():
        try:
            denom = int(raw_key)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid denomination key: {raw_key!r}",
            ) from e
        if denom not in DENOMINATION_SET:
            raise CashError(
                "invalid_denominations",
                f"unknown denomination: {denom} agorot",
            )
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid count for {denom}: {raw_count!r}",
            ) from e
        if count < 0:
            raise CashError("invalid_denominations", f"count for {denom} must be >= 0")
        total += denom * count
    ensure_multiple_of_10_agorot(total)
    return total


def _require_matching_delta_denoms(
    kind: str,
    amount_agorot: int,
    denominations: dict[str, Any] | dict[int, Any],
) -> int:
    """Return signed denom net; must equal ±amount for spend/receive."""
    net = denomination_deltas_net_agorot(denominations)
    if kind == "spend":
        if net != -amount_agorot:
            raise CashError(
                "denomination_mismatch",
                "denominations net must equal -amount_agorot for spend",
            )
    elif kind == "receive":
        if net != amount_agorot:
            raise CashError(
                "denomination_mismatch",
                "denominations net must equal amount_agorot for receive",
            )
    else:
        raise CashError("invalid_kind", "kind must be spend or receive")
    return net


def denomination_deltas_net_agorot(pieces: dict[str, Any] | dict[int, Any]) -> int:
    """Net agorot from signed piece deltas (Excel history rows)."""
    if not isinstance(pieces, dict):
        raise CashError("invalid_denominations", "denominations must be an object")
    total = 0
    for raw_key, raw_count in pieces.items():
        try:
            denom = int(raw_key)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid denomination key: {raw_key!r}",
            ) from e
        if denom not in DENOMINATION_SET:
            raise CashError(
                "invalid_denominations",
                f"unknown denomination: {denom} agorot",
            )
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid count for {denom}: {raw_count!r}",
            ) from e
        total += denom * count
    ensure_multiple_of_10_agorot(total)
    return total


def normalize_denominations_json(pieces: dict[str, Any] | dict[int, Any]) -> str:
    """Canonical JSON map with string keys for all known denominations (0 when missing)."""
    normalized: dict[str, int] = {str(d): 0 for d in DENOMINATION_AGOROT}
    if not isinstance(pieces, dict):
        raise CashError("invalid_denominations", "denominations must be an object")
    for raw_key, raw_count in pieces.items():
        try:
            denom = int(raw_key)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid denomination key: {raw_key!r}",
            ) from e
        if denom not in DENOMINATION_SET:
            raise CashError(
                "invalid_denominations",
                f"unknown denomination: {denom} agorot",
            )
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid count for {denom}: {raw_count!r}",
            ) from e
        if count < 0:
            raise CashError("invalid_denominations", f"count for {denom} must be >= 0")
        normalized[str(denom)] = count
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def normalize_delta_denominations_json(pieces: dict[str, Any] | dict[int, Any]) -> str:
    """JSON map of signed piece deltas (zeros omitted)."""
    if not isinstance(pieces, dict):
        raise CashError("invalid_denominations", "denominations must be an object")
    out: dict[str, int] = {}
    for raw_key, raw_count in pieces.items():
        try:
            denom = int(raw_key)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid denomination key: {raw_key!r}",
            ) from e
        if denom not in DENOMINATION_SET:
            raise CashError(
                "invalid_denominations",
                f"unknown denomination: {denom} agorot",
            )
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as e:
            raise CashError(
                "invalid_denominations",
                f"invalid count for {denom}: {raw_count!r}",
            ) from e
        if count != 0:
            out[str(denom)] = count
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def parse_iso_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise CashError("invalid_date", "event_date required (YYYY-MM-DD)")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as e:
        raise CashError("invalid_date", "event_date must be YYYY-MM-DD") from e
    return parsed.isoformat()


def parse_flexible_date(value: str) -> str:
    """Accept ``YYYY-MM-DD`` or ``DD/MM/YYYY`` (Excel)."""
    text = (value or "").strip()
    if not text:
        raise CashError("invalid_date", "event_date required")
    m = _DATE_DMY.match(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError as e:
            raise CashError("invalid_date", f"invalid date: {text}") from e
    return parse_iso_date(text)


def _looks_like_money_or_balance(cell: str) -> bool:
    s = cell.strip()
    if not s:
        return False
    if "₪" in s:
        return True
    cleaned = s.replace(",", "").replace("−", "-").replace(" ", "")
    # Decimal money without shekel sign
    if re.fullmatch(r"-?\d+\.\d+", cleaned):
        return True
    return False


def _parse_trailing_texts(rest: list[str]) -> tuple[str | None, str | None]:
    texts: list[str] = []
    for cell in rest:
        if not cell.strip():
            continue
        if _looks_like_money_or_balance(cell):
            continue
        texts.append(cell.strip())
    category = texts[0] if texts else None
    note = texts[1] if len(texts) > 1 else None
    return category, note


def parse_month(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return date.today().strftime("%Y-%m")
    if len(text) != 7 or text[4] != "-":
        raise CashError("invalid_month", "month must be YYYY-MM")
    try:
        date.fromisoformat(text + "-01")
    except ValueError as e:
        raise CashError("invalid_month", "month must be YYYY-MM") from e
    return text


@contextmanager
def _immediate_write(conn: sqlite3.Connection, *, commit: bool):
    """Serialize writers with BEGIN IMMEDIATE when this call owns the transaction."""
    if not commit:
        yield
        return
    old = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.isolation_level = old


def balance_agorot(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(delta_agorot), 0) FROM cash_event").fetchone()
    return int(row[0] or 0) if row else 0


def current_piece_inventory(conn: sqlite3.Connection) -> dict[str, int]:
    """Reconstruct piece counts by walking history.

    ``count`` replaces the inventory; spend/receive with a denominations map
    apply signed piece deltas. Events without denominations leave the mix unchanged.
    """
    inv: dict[str, int] = {str(d): 0 for d in DENOMINATION_AGOROT}
    rows = conn.execute(
        """
        SELECT kind, denominations
        FROM cash_event
        ORDER BY event_date ASC, id ASC
        """
    ).fetchall()
    for kind, denom_raw in rows:
        if not denom_raw:
            continue
        try:
            parsed = json.loads(denom_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        if str(kind) == "count":
            inv = {str(d): 0 for d in DENOMINATION_AGOROT}
            for raw_key, raw_count in parsed.items():
                try:
                    denom = int(raw_key)
                    count = int(raw_count)
                except (TypeError, ValueError):
                    continue
                if denom in DENOMINATION_SET:
                    inv[str(denom)] = max(0, count)
        else:
            for raw_key, raw_count in parsed.items():
                try:
                    denom = int(raw_key)
                    count = int(raw_count)
                except (TypeError, ValueError):
                    continue
                if denom not in DENOMINATION_SET:
                    continue
                key = str(denom)
                inv[key] = inv.get(key, 0) + count
    return inv


def inventory_split_agorot(inv: dict[str, int]) -> dict[str, int]:
    paper = 0
    coins = 0
    for raw_key, raw_count in inv.items():
        try:
            denom = int(raw_key)
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        value = denom * count
        if denom in PAPER_SET:
            paper += value
        elif denom in COIN_SET:
            coins += value
    return {
        "paper_agorot": paper,
        "coin_agorot": coins,
        "pieces_agorot": paper + coins,
    }


def last_count_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT event_date FROM cash_event WHERE kind = 'count' "
        "ORDER BY event_date DESC, id DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def insert_spend_or_receive(
    conn: sqlite3.Connection,
    *,
    kind: str,
    event_date: str,
    amount_agorot: int,
    note: str | None = None,
    category: str | None = None,
    denominations: dict[str, Any] | dict[int, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    if kind not in ("spend", "receive"):
        raise CashError("invalid_kind", "kind must be spend or receive")
    if not isinstance(amount_agorot, int) or isinstance(amount_agorot, bool):
        raise CashError("invalid_agorot", "amount_agorot must be a positive integer")
    if amount_agorot <= 0:
        raise CashError("invalid_agorot", "amount_agorot must be > 0")
    ensure_multiple_of_10_agorot(amount_agorot)
    iso = parse_flexible_date(event_date)
    delta = -amount_agorot if kind == "spend" else amount_agorot
    note_val = _clean_optional_text(note)
    cat_val = _clean_optional_text(category)
    denom_json = None
    if denominations:
        _require_matching_delta_denoms(kind, amount_agorot, denominations)
        denom_json = normalize_delta_denominations_json(denominations)
    with _immediate_write(conn, commit=commit):
        cur = conn.execute(
            """
            INSERT INTO cash_event (
                event_date, kind, delta_agorot, counted_agorot, expected_agorot,
                denominations, note, category
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (iso, kind, delta, denom_json, note_val, cat_val),
        )
        event_id = int(cur.lastrowid)
    return get_event(conn, event_id)


def insert_count(
    conn: sqlite3.Connection,
    *,
    event_date: str,
    denominations: dict[str, Any] | dict[int, Any],
    note: str | None = None,
    category: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    iso = parse_flexible_date(event_date)
    denom_json = normalize_denominations_json(denominations)
    counted = denominations_total_agorot(denominations)
    with _immediate_write(conn, commit=commit):
        expected = balance_agorot(conn)
        delta = counted - expected
        note_val = _clean_optional_text(note)
        cat_val = _clean_optional_text(category)
        cur = conn.execute(
            """
            INSERT INTO cash_event (
                event_date, kind, delta_agorot, counted_agorot, expected_agorot,
                denominations, note, category
            ) VALUES (?, 'count', ?, ?, ?, ?, ?, ?)
            """,
            (iso, delta, counted, expected, denom_json, note_val, cat_val),
        )
        event_id = int(cur.lastrowid)
    return get_event(conn, event_id)


def patch_event(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    category: str | None | object = ...,
    note: str | None | object = ...,
    event_date: str | None | object = ...,
    amount_agorot: int | None | object = ...,
    denominations: dict[str, Any] | dict[int, Any] | None | object = ...,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, kind, delta_agorot, denominations FROM cash_event WHERE id = ?",
        (int(event_id),),
    ).fetchone()
    if not row:
        raise CashError("not_found", f"cash event {event_id} not found")
    kind = str(row[1])
    existing_denoms = row[3]
    updates: list[str] = []
    params: list[Any] = []
    if category is not ...:
        updates.append("category = ?")
        params.append(_clean_optional_text(category if isinstance(category, str) else None))
    if note is not ...:
        updates.append("note = ?")
        params.append(_clean_optional_text(note if isinstance(note, str) else None))
    if event_date is not ...:
        if not isinstance(event_date, str):
            raise CashError("invalid_date", "event_date must be a string")
        updates.append("event_date = ?")
        params.append(parse_flexible_date(event_date))
    if amount_agorot is not ...:
        if kind not in ("spend", "receive"):
            raise CashError("invalid_patch", "amount can only be edited on spend/receive")
        if isinstance(amount_agorot, bool) or not isinstance(amount_agorot, int):
            raise CashError("invalid_agorot", "amount_agorot must be a positive integer")
        if amount_agorot <= 0:
            raise CashError("invalid_agorot", "amount_agorot must be > 0")
        ensure_multiple_of_10_agorot(amount_agorot)
        delta = -amount_agorot if kind == "spend" else amount_agorot
        updates.append("delta_agorot = ?")
        params.append(delta)
        if denominations is not ... and denominations is not None:
            if not isinstance(denominations, dict):
                raise CashError("invalid_denominations", "denominations must be an object")
            _require_matching_delta_denoms(kind, amount_agorot, denominations)
            updates.append("denominations = ?")
            params.append(normalize_delta_denominations_json(denominations))
        elif existing_denoms:
            raise CashError(
                "denominations_required",
                "amount patch on an event with piece counts requires matching denominations",
            )
    elif denominations is not ...:
        raise CashError(
            "invalid_patch",
            "denominations can only be updated together with amount_agorot",
        )
    if not updates:
        raise CashError("empty_patch", "Provide category, note, event_date, and/or amount_agorot")
    params.append(int(event_id))
    conn.execute(f"UPDATE cash_event SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return get_event(conn, int(event_id))


def delete_event(conn: sqlite3.Connection, event_id: int) -> bool:
    cur = conn.execute("DELETE FROM cash_event WHERE id = ?", (int(event_id),))
    conn.commit()
    return cur.rowcount > 0


def get_event(conn: sqlite3.Connection, event_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, event_date, kind, delta_agorot, counted_agorot, expected_agorot,
               denominations, note, created_at, category
        FROM cash_event WHERE id = ?
        """,
        (int(event_id),),
    ).fetchone()
    if not row:
        raise CashError("not_found", f"cash event {event_id} not found")
    return _row_to_dict(row)


def last_activity_month(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT event_date FROM cash_event ORDER BY event_date DESC, id DESC LIMIT 1"
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])[:7]


_LIST_ALL_CAP = 2000


def list_events(
    conn: sqlite3.Connection,
    month: str | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return events oldest-first with ``balance_after_agorot``.

    A ``YYYY-MM`` month uses one opening-balance aggregate then that month's rows.
    ``month is None`` (all history) uses a window SUM and is capped.
    """
    ym = parse_month(month) if month else None
    if ym is not None:
        opening = int(
            conn.execute(
                "SELECT COALESCE(SUM(delta_agorot), 0) FROM cash_event WHERE event_date < ?",
                (f"{ym}-01",),
            ).fetchone()[0]
            or 0
        )
        rows = conn.execute(
            """
            SELECT id, event_date, kind, delta_agorot, counted_agorot, expected_agorot,
                   denominations, note, created_at, category
            FROM cash_event
            WHERE substr(event_date, 1, 7) = ?
            ORDER BY event_date ASC, id ASC
            """,
            (ym,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        running = opening
        for row in rows:
            running += int(row[3] or 0)
            item = _row_to_dict(row)
            item["balance_after_agorot"] = running
            out.append(item)
        return out

    cap = _LIST_ALL_CAP if limit is None else max(1, int(limit))
    off = max(0, int(offset))
    rows = conn.execute(
        """
        SELECT id, event_date, kind, delta_agorot, counted_agorot, expected_agorot,
               denominations, note, created_at, category,
               SUM(delta_agorot) OVER (ORDER BY event_date ASC, id ASC) AS balance_after_agorot
        FROM cash_event
        ORDER BY event_date ASC, id ASC
        LIMIT ? OFFSET ?
        """,
        (cap, off),
    ).fetchall()
    out = []
    for row in rows:
        item = _row_to_dict(row)
        item["balance_after_agorot"] = int(row[10] or 0)
        out.append(item)
    return out


def list_events_for_month(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    """Return events in ``month`` oldest-first, each with ``balance_after_agorot``."""
    return list_events(conn, month=month)


def summary(conn: sqlite3.Connection, month: str | None = None) -> dict[str, Any]:
    activity = last_activity_month(conn)
    if month:
        ym = parse_month(month)
    else:
        ym = activity or date.today().strftime("%Y-%m")
    inv = current_piece_inventory(conn)
    split = inventory_split_agorot(inv)
    return {
        "ok": True,
        "balance_agorot": balance_agorot(conn),
        "paper_agorot": split["paper_agorot"],
        "coin_agorot": split["coin_agorot"],
        "pieces_agorot": split["pieces_agorot"],
        "inventory": {str(d): int(inv.get(str(d), 0)) for d in DENOMINATION_AGOROT},
        "last_count_date": last_count_date(conn),
        "last_activity_month": activity,
        "month": ym,
        "denominations_agorot": list(DENOMINATION_AGOROT),
        "paper_denominations_agorot": list(PAPER_DENOMINATION_AGOROT),
        "coin_denominations_agorot": list(COIN_DENOMINATION_AGOROT),
    }


def _parse_int_cell(cell: str) -> int | None:
    s = (cell or "").strip()
    if not s:
        return None
    s = s.replace(",", "").replace("−", "-")
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_excel_backfill_text(
    text: str,
    *,
    first_row_is_count: bool = True,
) -> list[dict[str, Any]]:
    """Parse TSV/CSV Excel-style cash history into insertable row dicts.

    Expected data columns after date (ascending denoms):
    ``0.1  0.5  1  2  5  10  20  50  100  200  [category] [note]``

    Trailing ``שווי תנועה`` / ``יתרה`` money columns are ignored when they look like ₪ amounts.
    Header / inventory summary rows without a parseable date are skipped.
    """
    if not (text or "").strip():
        raise CashError("empty_paste", "Paste is empty")

    lines = [ln for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    parsed: list[dict[str, Any]] = []
    for ln in lines:
        raw = ln.strip()
        if not raw:
            continue
        if "\t" in ln:
            cells = [c.strip() for c in ln.rstrip("\n").split("\t")]
        else:
            cells = [c.strip() for c in ln.rstrip("\n").split(",")]
        if not cells or not cells[0]:
            continue
        try:
            event_date = parse_flexible_date(cells[0])
        except CashError:
            continue  # header / summary row

        denom_cells = cells[1 : 1 + len(EXCEL_DENOM_ORDER_AGOROT)]
        while len(denom_cells) < len(EXCEL_DENOM_ORDER_AGOROT):
            denom_cells.append("")

        pieces: dict[str, int] = {}
        for denom, cell in zip(EXCEL_DENOM_ORDER_AGOROT, denom_cells, strict=True):
            n = _parse_int_cell(cell)
            if n is not None and n != 0:
                pieces[str(denom)] = n

        rest = cells[1 + len(EXCEL_DENOM_ORDER_AGOROT) :]
        category, note = _parse_trailing_texts(rest)

        parsed.append(
            {
                "event_date": event_date,
                "pieces": pieces,
                "category": category,
                "note": note,
            }
        )

    if not parsed:
        raise CashError("no_rows", "No dated rows found in paste")

    out: list[dict[str, Any]] = []
    for i, row in enumerate(parsed):
        pieces = row["pieces"]
        if i == 0 and first_row_is_count:
            # Absolute inventory — empty pieces = 0 cash
            out.append(
                {
                    "kind": "count",
                    "event_date": row["event_date"],
                    "denominations": {str(d): int(pieces.get(str(d), 0)) for d in EXCEL_DENOM_ORDER_AGOROT},
                    "category": row["category"],
                    "note": row["note"],
                }
            )
            continue

        net = denomination_deltas_net_agorot(pieces) if pieces else 0
        if net == 0:
            continue
        kind = "receive" if net > 0 else "spend"
        out.append(
            {
                "kind": kind,
                "event_date": row["event_date"],
                "amount_agorot": abs(net),
                "denominations": pieces,
                "category": row["category"],
                "note": row["note"],
            }
        )
    if not out:
        raise CashError("no_rows", "No importable cash movements in paste")
    return out


def apply_backfill(
    conn: sqlite3.Connection,
    text: str,
    *,
    first_row_is_count: bool = True,
) -> dict[str, Any]:
    """Insert parsed Excel rows in chronological order (as pasted)."""
    rows = parse_excel_backfill_text(text, first_row_is_count=first_row_is_count)
    # Sort by date then original order
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda t: (t[1]["event_date"], t[0]))
    created: list[dict[str, Any]] = []
    with _immediate_write(conn, commit=True):
        for _, row in indexed:
            if row["kind"] == "count":
                created.append(
                    insert_count(
                        conn,
                        event_date=row["event_date"],
                        denominations=row["denominations"],
                        category=row.get("category"),
                        note=row.get("note"),
                        commit=False,
                    )
                )
            else:
                created.append(
                    insert_spend_or_receive(
                        conn,
                        kind=row["kind"],
                        event_date=row["event_date"],
                        amount_agorot=int(row["amount_agorot"]),
                        category=row.get("category"),
                        note=row.get("note"),
                        denominations=row.get("denominations"),
                        commit=False,
                    )
                )
    return {
        "ok": True,
        "inserted": len(created),
        "events": created,
        "balance_agorot": balance_agorot(conn),
    }


def _row_to_dict(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    denom_raw = row[6]
    denom_obj: dict[str, int] | None = None
    if denom_raw:
        try:
            parsed = json.loads(denom_raw)
            if isinstance(parsed, dict):
                denom_obj = {str(k): int(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            denom_obj = None
    category = None
    if len(row) > 9:
        category = row[9]
    return {
        "id": int(row[0]),
        "event_date": str(row[1]),
        "kind": str(row[2]),
        "delta_agorot": int(row[3] or 0),
        "counted_agorot": int(row[4]) if row[4] is not None else None,
        "expected_agorot": int(row[5]) if row[5] is not None else None,
        "denominations": denom_obj,
        "note": row[7],
        "created_at": str(row[8]) if row[8] is not None else None,
        "category": category,
    }
