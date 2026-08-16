"""Paginated ledger transaction search for ``GET /api/ledger/search``."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import parse_qs

import config
from ledger import (
    LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR,
    LEDGER_SQL_EFFECTIVE_YM_EXPR,
    LEDGER_SQL_TX_INCLUDED,
    ledger_connect_readonly,
)

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_AMOUNT_EPS = 0.005

_SEARCH_COLUMNS = (
    "id",
    "תאריך",
    "בחובה",
    "בזכות",
    "מקור עסקה",
    "פירוט נוסף",
    "תאור מורחב",
    "4 ספרות",
    "fingerprint",
    "קטגוריה",
    "notes",
    "statement_month",
    "ingested_at",
    "category_updated_at",
    "data_updated_at",
    "excluded_from_calculations",
)

_TEXT_LIKE_COLS = (
    '"מקור עסקה"',
    '"פירוט נוסף"',
    '"תאור מורחב"',
    "notes",
    '"קטגוריה"',
    '"4 ספרות"',
    "ingested_at",
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_EU_DATE_RE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")


def _ledger_path() -> str:
    return config.ledger_db_file


def _first(qs: dict[str, list[str]], key: str) -> str | None:
    vals = qs.get(key)
    if not vals:
        return None
    s = (vals[0] or "").strip()
    return s if s else None


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(raw: str | None, default: int, *, lo: int, hi: int) -> int:
    if raw is None:
        return default
    try:
        n = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = raw.strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _escape_like(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_number_token(token: str) -> float | None:
    raw = token.strip()
    if not re.match(r"^-?\d+(?:[.,]\d+)?$", raw):
        return None
    try:
        return float(raw.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _normalize_date_token(token: str) -> tuple[str | None, str | None]:
    """Return ``(iso_date, iso_month_prefix)`` for date-shaped tokens."""
    t = token.strip()
    if _ISO_DATE_RE.match(t):
        return t, t[:7]
    if _ISO_MONTH_RE.match(t):
        return None, t
    m = _EU_DATE_RE.match(t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            iso = f"{y:04d}-{mo:02d}-{d:02d}"
            return iso, f"{y:04d}-{mo:02d}"
    return None, None


def _token_clause(token: str) -> tuple[str, list[Any]]:
    """Build OR-clause for one free-text token; caller ANDs tokens together."""
    parts: list[str] = []
    params: list[Any] = []

    num = _parse_number_token(token)
    iso_date, iso_month = _normalize_date_token(token)
    # Month-only when token is YYYY-MM (not a full date that also yields a month prefix).
    month_only = iso_month is not None and iso_date is None

    if num is not None:
        parts.append(
            f'(ABS(COALESCE("בחובה", 0) - ?) <= {_AMOUNT_EPS} '
            f'OR ABS(COALESCE("בזכות", 0) - ?) <= {_AMOUNT_EPS})'
        )
        params.extend([num, num])
        if num == int(num) and abs(num) < 1_000_000_000:
            parts.append("id = ?")
            params.append(int(num))
        # Card last-4 / short digit strings still searchable as text
        like = f"%{_escape_like(token)}%"
        parts.append("COALESCE(\"4 ספרות\", '') LIKE ? ESCAPE '\\'")
        params.append(like)
    else:
        like = f"%{_escape_like(token)}%"
        text_ors = " OR ".join(
            f"COALESCE({col}, '') LIKE ? ESCAPE '\\'" for col in _TEXT_LIKE_COLS
        )
        parts.append(f"({text_ors})")
        params.extend([like] * len(_TEXT_LIKE_COLS))
        # Fingerprint / id as text only for non-numeric tokens
        parts.append("COALESCE(fingerprint, '') LIKE ? ESCAPE '\\'")
        params.append(like)
        parts.append("CAST(id AS TEXT) LIKE ? ESCAPE '\\'")
        params.append(like)

    if iso_date is not None:
        # Match purchase day or effective (statement) day for installments.
        parts.append(
            f'("תאריך" = ? OR ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) = ?)'
        )
        params.extend([iso_date, iso_date])
    elif month_only and iso_month is not None:
        parts.append(f"({LEDGER_SQL_EFFECTIVE_YM_EXPR}) = ?")
        params.append(iso_month)

    return "(" + " OR ".join(parts) + ")", params


def _build_where(
    *,
    q: str | None,
    date_from: str | None,
    date_to: str | None,
    amount_min: float | None,
    amount_max: float | None,
    direction: str,
    include_excluded: bool,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if not include_excluded:
        clauses.append(LEDGER_SQL_TX_INCLUDED)

    if date_from:
        clauses.append(f"({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) <= ?")
        params.append(date_to)

    if amount_min is not None:
        clauses.append('MAX(COALESCE("בחובה", 0), COALESCE("בזכות", 0)) >= ?')
        params.append(amount_min)
    if amount_max is not None:
        clauses.append('MAX(COALESCE("בחובה", 0), COALESCE("בזכות", 0)) <= ?')
        params.append(amount_max)

    if direction == "expense":
        clauses.append('COALESCE("בחובה", 0) > 0')
    elif direction == "income":
        clauses.append('COALESCE("בזכות", 0) > 0')

    if q:
        for token in q.split():
            if not token:
                continue
            clause, tparams = _token_clause(token)
            clauses.append(clause)
            params.extend(tparams)

    if not clauses:
        return "1=1", []
    return " AND ".join(clauses), params


def search_ledger(qs: dict[str, list[str]] | None = None) -> tuple[int, dict[str, Any]]:
    """Run search; return ``(http_status, payload)``."""
    qs = qs or {}
    path = _ledger_path()
    if not os.path.isfile(path):
        return 503, {
            "ok": False,
            "error": "unavailable",
            "message": "Ledger data not available.",
            "total": 0,
            "limit": _DEFAULT_LIMIT,
            "offset": 0,
            "columns": list(_SEARCH_COLUMNS),
            "rows": [],
        }

    q = _first(qs, "q")
    date_from = _first(qs, "date_from")
    date_to = _first(qs, "date_to")
    amount_min = _parse_float(_first(qs, "amount_min"))
    amount_max = _parse_float(_first(qs, "amount_max"))
    direction_raw = (_first(qs, "direction") or "all").lower()
    direction = direction_raw if direction_raw in {"all", "expense", "income"} else "all"
    include_excluded = _parse_bool(_first(qs, "include_excluded"), False)
    limit = _parse_int(_first(qs, "limit"), _DEFAULT_LIMIT, lo=1, hi=_MAX_LIMIT)
    offset = _parse_int(_first(qs, "offset"), 0, lo=0, hi=10_000_000)

    if date_from and not _ISO_DATE_RE.match(date_from):
        date_from = None
    if date_to and not _ISO_DATE_RE.match(date_to):
        date_to = None

    where_sql, params = _build_where(
        q=q,
        date_from=date_from,
        date_to=date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        direction=direction,
        include_excluded=include_excluded,
    )

    col_list = ", ".join(
        [
            "id",
            '"תאריך"',
            '"בחובה"',
            '"בזכות"',
            '"מקור עסקה"',
            '"פירוט נוסף"',
            '"תאור מורחב"',
            '"4 ספרות"',
            "fingerprint",
            '"קטגוריה"',
            "notes",
            "statement_month",
            "ingested_at",
            "category_updated_at",
            "data_updated_at",
            "excluded_from_calculations",
        ]
    )

    count_sql = f"SELECT COUNT(*) FROM ledger_transaction WHERE {where_sql}"
    data_sql = (
        f"SELECT {col_list} FROM ledger_transaction WHERE {where_sql} "
        f"ORDER BY ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) DESC, id DESC "
        f"LIMIT ? OFFSET ?"
    )

    conn = ledger_connect_readonly(path)
    try:
        total = int(conn.execute(count_sql, params).fetchone()[0])
        cur = conn.execute(data_sql, [*params, limit, offset])
        cols = [d[0] for d in cur.description] if cur.description else list(_SEARCH_COLUMNS)
        rows: list[dict[str, Any]] = []
        for tup in cur.fetchall():
            rows.append({cols[i]: tup[i] for i in range(len(cols))})
    finally:
        conn.close()

    return 200, {
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "columns": cols,
        "rows": rows,
    }


def handle_search_request(query_string: str) -> tuple[int, dict[str, Any]]:
    qs = parse_qs(query_string, keep_blank_values=False)
    return search_ledger(qs)
