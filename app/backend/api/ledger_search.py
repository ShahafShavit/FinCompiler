"""Paginated ledger transaction search for ``GET /api/ledger/search``."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import Any
from urllib.parse import parse_qs

from ledger import (
    LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR,
    LEDGER_SQL_EFFECTIVE_YM_EXPR,
    LEDGER_SQL_TX_INCLUDED,
    ledger_connect_readonly,
)
from ledger.card_payer import payer_sql

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
    from ledger.books import active_ledger_path

    return active_ledger_path()


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
    ids: list[int] | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if not include_excluded:
        clauses.append(LEDGER_SQL_TX_INCLUDED)

    if ids:
        placeholders = ",".join("?" for _ in ids)
        clauses.append(f"id IN ({placeholders})")
        params.extend(ids)

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

    # When ids are provided, skip free-text q — caller wants exactly those rows.
    if q and not ids:
        for token in q.split():
            if not token:
                continue
            clause, tparams = _token_clause(token)
            clauses.append(clause)
            params.extend(tparams)

    if not clauses:
        return "1=1", []
    return " AND ".join(clauses), params


def _parse_ids(raw: str | None) -> list[int]:
    if not raw or not raw.strip():
        return []
    out: list[int] = []
    seen: set[int] = set()
    for part in re.split(r"[,;\s]+", raw.strip()):
        if not part:
            continue
        if not re.fullmatch(r"\d+", part):
            continue
        n = int(part)
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
        if len(out) >= 2000:
            break
    return out


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
    ids = _parse_ids(_first(qs, "ids"))
    date_from = _first(qs, "date_from")
    date_to = _first(qs, "date_to")
    amount_min = _parse_float(_first(qs, "amount_min"))
    amount_max = _parse_float(_first(qs, "amount_max"))
    direction_raw = (_first(qs, "direction") or "all").lower()
    direction = direction_raw if direction_raw in {"all", "expense", "income"} else "all"
    include_excluded = _parse_bool(_first(qs, "include_excluded"), False)
    # Allow a larger page when fetching an explicit id set (merged plans, etc.)
    max_limit = 2000 if ids else _MAX_LIMIT
    default_limit = min(len(ids), max_limit) if ids else _DEFAULT_LIMIT
    if ids and default_limit < 1:
        default_limit = _DEFAULT_LIMIT
    limit = _parse_int(_first(qs, "limit"), default_limit if ids else _DEFAULT_LIMIT, lo=1, hi=max_limit)
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
        ids=ids or None,
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
            f"{payer_sql()} AS payer",
        ]
    )

    count_sql = f"SELECT COUNT(*) FROM ledger_transaction WHERE {where_sql}"
    data_sql = (
        f"SELECT {col_list}, COUNT(*) OVER() AS _search_total "
        f"FROM ledger_transaction WHERE {where_sql} "
        f"ORDER BY ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) DESC, id DESC "
        f"LIMIT ? OFFSET ?"
    )

    conn = ledger_connect_readonly(path)
    try:
        # Prefer one windowed SELECT. Fall back to COUNT + page if OVER() is unavailable.
        try:
            cur = conn.execute(data_sql, [*params, limit, offset])
            cols_raw = [d[0] for d in cur.description] if cur.description else list(_SEARCH_COLUMNS)
            fetched = cur.fetchall()
        except sqlite3.OperationalError:
            total = int(conn.execute(count_sql, params).fetchone()[0])
            cur = conn.execute(
                f"SELECT {col_list} FROM ledger_transaction WHERE {where_sql} "
                f"ORDER BY ({LEDGER_SQL_EFFECTIVE_TX_DATE_EXPR}) DESC, id DESC "
                f"LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
            cols = [d[0] for d in cur.description] if cur.description else list(_SEARCH_COLUMNS)
            rows = [{cols[i]: tup[i] for i in range(len(cols))} for tup in cur.fetchall()]
            return 200, {
                "ok": True,
                "total": total,
                "limit": limit,
                "offset": offset,
                "columns": cols,
                "rows": rows,
            }
        cols = [c for c in cols_raw if c != "_search_total"]
        total = int(fetched[0][-1]) if fetched else 0
        if not fetched and offset > 0:
            total = int(conn.execute(count_sql, params).fetchone()[0])
        rows: list[dict[str, Any]] = []
        for tup in fetched:
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


_BULK_PATCH_ALLOWED = frozenset({"קטגוריה", "excluded_from_calculations"})
_BULK_MAX_IDS = 500


def handle_bulk_patch(raw: bytes) -> tuple[int, dict[str, Any]]:
    """``POST /api/ledger/bulk-patch`` — safe keys only (category / exclude)."""
    import json

    from ledger import LEDGER_TX_PATCH_FINGERPRINT_KEYS, patch_ledger_transaction_by_id

    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "invalid JSON"}

    if not isinstance(data, dict):
        return 400, {"ok": False, "error": "validation_error", "message": "body must be an object"}

    ids_raw = data.get("ids")
    patch = data.get("patch")
    if not isinstance(ids_raw, list) or not ids_raw:
        return 400, {"ok": False, "error": "validation_error", "message": "ids must be a non-empty array"}
    if not isinstance(patch, dict) or not patch:
        return 400, {"ok": False, "error": "validation_error", "message": "patch must be a non-empty object"}

    unknown = set(patch.keys()) - _BULK_PATCH_ALLOWED
    if unknown:
        return 400, {
            "ok": False,
            "error": "validation_error",
            "message": f"bulk-patch allows only {sorted(_BULK_PATCH_ALLOWED)}; got {sorted(unknown)}",
        }
    fp_hit = set(patch.keys()) & LEDGER_TX_PATCH_FINGERPRINT_KEYS
    if fp_hit:
        return 400, {
            "ok": False,
            "error": "validation_error",
            "message": f"fingerprint-driving fields not allowed in bulk-patch: {sorted(fp_hit)}",
        }

    ids: list[int] = []
    for x in ids_raw:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            return 400, {
                "ok": False,
                "error": "validation_error",
                "message": f"invalid id: {x!r}",
            }
    if len(ids) > _BULK_MAX_IDS:
        return 400, {
            "ok": False,
            "error": "validation_error",
            "message": f"at most {_BULK_MAX_IDS} ids per request",
        }

    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "not_found", "message": "ledger database missing"}

    updated = 0
    errors: list[dict[str, Any]] = []
    for rid in ids:
        out = patch_ledger_transaction_by_id(path, rid, patch)
        if out.get("ok"):
            updated += 1
        else:
            errors.append(
                {
                    "id": rid,
                    "error": out.get("error"),
                    "message": out.get("message"),
                }
            )

    ok = updated > 0 and not errors
    status = 200 if not errors else (207 if updated else 400)
    return status, {
        "ok": ok or (updated > 0 and len(errors) < len(ids)),
        "updated": updated,
        "requested": len(ids),
        "errors": errors,
    }
