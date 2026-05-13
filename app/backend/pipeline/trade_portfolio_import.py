"""Load trade-portfolio snapshot workbooks (SpreadsheetML XML as .xls) into SQLite ``trade_portfolio_position``."""

from __future__ import annotations

import glob
import logging
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd

import config
from ledger.store import migrate_ledger_db
from pipeline.compiler import parse_post_ingest_date_scalar

log = logging.getLogger(__name__)

Sink = Optional[Callable[[str], None]]

_MULT_UPSERT_SQL = """
INSERT INTO trade_portfolio_position_multiplier (
    portfolio_account, security_number, security_name, price_multiplier
) VALUES (?,?,?,?)
ON CONFLICT(portfolio_account, security_number) DO UPDATE SET
    price_multiplier = excluded.price_multiplier,
    security_name = COALESCE(
        excluded.security_name,
        trade_portfolio_position_multiplier.security_name
    );
"""


def _row_price_multiplier(r: dict[str, Any]) -> float | None:
    """Return a value to persist, or None to omit a child row (effective multiplier 1)."""
    if "price_multiplier" not in r:
        return None
    raw = r.get("price_multiplier")
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        m = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"invalid price_multiplier: {raw!r}") from e
    if m <= 0:
        raise ValueError(f"price_multiplier must be > 0, got {raw!r}")
    if m == 1.0:
        return None
    return m


def _notify(msg: str, sink: Sink) -> None:
    log.info(msg)
    if sink:
        sink(msg)


_SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"


def _tag(local: str) -> str:
    return f"{{{_SS_NS}}}{local}"


def _norm_header(s: object) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip())


# Hebrew export headers (מבט אישי - אחזקות) → DB column names (excluding snapshot/portfolio/imported_at).
_HEADER_ALIASES: dict[str, str] = {
    "מספר נייר": "security_number",
    "שם הנייר": "security_name",
    "שער קניה ממוצע": "avg_purchase_price",
    "כמות אחזקה": "quantity",
    "שער אחרון": "last_price",
    "שווי אחזקה ב ₪": "value_ils",
    "שווי אחזקה ב₪": "value_ils",
    "% שינוי יומי": "daily_change_pct",
    "רווח ב-%": "profit_pct",
    "רווח ב ₪": "profit_ils",
    "רווח ב₪": "profit_ils",
    "% מהתיק": "pct_of_portfolio",
    "%  מהתיק": "pct_of_portfolio",
    "שער בסיס": "basis_price",
    "מס התיק": "_portfolio_from_row",
}


def _is_spreadsheetml_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(400)
    except OSError:
        return False
    if not head:
        return False
    probe = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if b"urn:schemas-microsoft-com:office:spreadsheet" in head:
        return True
    if probe.startswith(b"<?xml") and b"Excel.Sheet" in head:
        return True
    return False


# Ampersands in cell text (e.g. "S&P 500") must be escaped in XML; bank exports are often malformed.
_AMP_NOT_ENTITY = re.compile(r"&(?!([a-zA-Z][a-zA-Z0-9.:-]{0,63}|#[0-9]+|#x[0-9a-fA-F]+);)")


def _escape_bare_ampersands_xml(text: str) -> str:
    return _AMP_NOT_ENTITY.sub("&amp;", text)


def _load_spreadsheetml_element_tree(path: str) -> ET.ElementTree:
    """
    Normalize bank SpreadsheetML before :func:`ET.fromstring`:

    - Strip BOM / leading whitespace so ``<?xml`` is at the start (``ET.parse`` path requirement).
    - Escape bare ``&`` in text (e.g. ``S&P``) — exports are often not well-formed XML.
    """
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig", errors="replace")
    text = text.lstrip()
    if not text.startswith("<?xml") and not text.startswith("<Workbook"):
        i_xml = text.find("<?xml")
        i_wb = text.find("<Workbook")
        candidates = [i for i in (i_xml, i_wb) if i >= 0]
        if not candidates:
            raise ValueError(f"no <?xml or <Workbook in {path!r}")
        text = text[min(candidates) :]
    text = _escape_bare_ampersands_xml(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"invalid SpreadsheetML XML in {path!r}: {e}") from e
    return ET.ElementTree(root)


def _cell_value(cell: ET.Element) -> Any:
    data = cell.find(_tag("Data"))
    if data is None:
        return None
    raw = (data.text or "").strip()
    typ = data.get(f"{{{_SS_NS}}}Type") or "String"
    if typ == "Number":
        if raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return raw if raw else None


def _row_cell_values(row_el: ET.Element) -> list[Any]:
    col_idx = 0
    out: list[Any | None] = []
    for cell in row_el.findall(_tag("Cell")):
        idx_attr = cell.get(f"{{{_SS_NS}}}Index")
        if idx_attr is not None:
            col_idx = int(idx_attr) - 1
        while len(out) <= col_idx:
            out.append(None)
        out[col_idx] = _cell_value(cell)
        col_idx += 1
    return out


def _pair_after_label(flat: list[Any], label: str) -> str | None:
    for i, c in enumerate(flat):
        if c is None:
            continue
        if str(c).strip() == label and i + 1 < len(flat):
            nxt = flat[i + 1]
            if nxt is None:
                return None
            return str(nxt).strip()
    return None


def _snapshot_date_iso(raw: object) -> str:
    ts = parse_post_ingest_date_scalar(raw)
    if pd.isna(ts):
        raise ValueError(f"invalid snapshot date: {raw!r}")
    return ts.strftime("%Y-%m-%d")


def parse_trade_portfolio_workbook(path: str) -> tuple[str, str, list[dict[str, Any]]]:
    """
    Parse a trade-portfolio export into rows ready for ``trade_portfolio_position``.

    Returns (snapshot_date_iso, portfolio_account, row_dicts) where each row_dict holds
    column names matching the SQLite table (except imported_at).
    """
    if not _is_spreadsheetml_file(path):
        raise ValueError(f"not a SpreadsheetML workbook: {path}")

    tree = _load_spreadsheetml_element_tree(path)
    root = tree.getroot()
    table = root.find(f".//{_tag('Table')}")
    if table is None:
        raise ValueError(f"no Table in workbook: {path}")

    snapshot_raw: str | None = None
    portfolio_meta: str | None = None
    header_fields: list[str | None] | None = None
    data_rows: list[dict[str, Any]] = []

    for row_el in table.findall(_tag("Row")):
        cells = _row_cell_values(row_el)
        if not any(v is not None and str(v).strip() != "" for v in cells):
            continue
        flat = cells
        if snapshot_raw is None or portfolio_meta is None:
            sd = _pair_after_label(flat, "תאריך:")
            if sd:
                snapshot_raw = sd
            pa = _pair_after_label(flat, "תיק:")
            if pa:
                portfolio_meta = pa

        if header_fields is None:
            h0 = flat[0] if flat else None
            if h0 is not None and str(h0).strip() == "מספר נייר":
                header_fields = []
                for c in flat:
                    nh = _norm_header(c)
                    header_fields.append(_HEADER_ALIASES.get(nh))
                continue

        if header_fields is None:
            continue

        rec: dict[str, Any] = {}
        for idx, field in enumerate(header_fields):
            if field is None or idx >= len(flat):
                continue
            val = flat[idx]
            if field == "_portfolio_from_row":
                rec["_portfolio_from_row"] = val
            else:
                rec[field] = val

        sec = rec.get("security_number")
        if sec is None or str(sec).strip() == "":
            continue
        rec["security_number"] = str(sec).strip()

        pf = rec.pop("_portfolio_from_row", None)
        portfolio_account = (str(pf).strip() if pf is not None and str(pf).strip() else None) or portfolio_meta
        if not portfolio_account:
            raise ValueError(f"missing portfolio account for row security_number={rec['security_number']}")
        if snapshot_raw is None:
            raise ValueError("missing snapshot date (תאריך:) in workbook header")
        snap = _snapshot_date_iso(snapshot_raw)
        rec["snapshot_date"] = snap
        rec["portfolio_account"] = portfolio_account
        data_rows.append(rec)

    if not data_rows:
        raise ValueError(f"no position rows parsed from {path}")
    snap_final = _snapshot_date_iso(snapshot_raw)
    portfolio_final = portfolio_meta or data_rows[0]["portfolio_account"]
    return snap_final, portfolio_final, data_rows


def upsert_trade_portfolio_snapshot(
    rows: list[dict[str, Any]],
    *,
    db_path: str | None = None,
) -> dict[str, Any]:
    """
    Replace all positions for (snapshot_date, portfolio_account) then insert ``rows``.
    Each dict must include snapshot_date, portfolio_account, security_number; other columns optional.
    """
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)
    if not rows:
        return {"deleted": 0, "inserted": 0, "snapshot_date": None, "portfolio_account": None}

    snapshot_date = rows[0]["snapshot_date"]
    portfolio_account = rows[0]["portfolio_account"]
    if not all(r["snapshot_date"] == snapshot_date and r["portfolio_account"] == portfolio_account for r in rows):
        raise ValueError("batch must be single snapshot_date + portfolio_account")

    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sql = """
        INSERT INTO trade_portfolio_position (
            snapshot_date, portfolio_account, security_number, security_name,
            avg_purchase_price, quantity, last_price, value_ils,
            daily_change_pct, profit_pct, profit_ils, pct_of_portfolio, basis_price,
            imported_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "DELETE FROM trade_portfolio_position WHERE snapshot_date = ? AND portfolio_account = ?",
            (snapshot_date, portfolio_account),
        )
        deleted = cur.rowcount or 0
        batch = []
        mult_batch: list[tuple[Any, ...]] = []
        for r in rows:
            batch.append(
                (
                    r["snapshot_date"],
                    r["portfolio_account"],
                    r["security_number"],
                    r.get("security_name"),
                    r.get("avg_purchase_price"),
                    r.get("quantity"),
                    r.get("last_price"),
                    r.get("value_ils"),
                    r.get("daily_change_pct"),
                    r.get("profit_pct"),
                    r.get("profit_ils"),
                    r.get("pct_of_portfolio"),
                    r.get("basis_price"),
                    imported_at,
                )
            )
            pm = _row_price_multiplier(r)
            if pm is not None:
                mult_batch.append(
                    (r["portfolio_account"], r["security_number"], r.get("security_name"), pm)
                )
        conn.executemany(sql, batch)
        if mult_batch:
            conn.executemany(_MULT_UPSERT_SQL, mult_batch)
        conn.commit()
        return {
            "deleted": deleted,
            "inserted": len(batch),
            "snapshot_date": snapshot_date,
            "portfolio_account": portfolio_account,
        }
    finally:
        conn.close()


def import_trade_portfolio_file(path: str, *, db_path: str | None = None) -> dict[str, Any]:
    """Parse ``path`` and upsert into ``trade_portfolio_position``."""
    snap, _pf, rows = parse_trade_portfolio_workbook(path)
    log.info(
        "trade portfolio: %s -> snapshot %s positions=%s",
        path,
        snap,
        len(rows),
    )
    return upsert_trade_portfolio_snapshot(rows, db_path=db_path)


def default_import_paths() -> list[str]:
    """Prefer ``trade_portfolio_inbox``; fall back to shared ``data/input`` spreadsheets."""
    out: list[str] = []
    for pattern_dir in (config.trade_portfolio_inbox_dir, config.download_inbox_dir):
        pattern = os.path.join(pattern_dir, "*.xls*")
        out.extend(sorted(glob.glob(pattern)))
    # de-dup preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen and _is_spreadsheetml_file(p):
            seen.add(ap)
            uniq.append(p)
    return uniq


def resolve_default_trade_portfolio_path() -> str | None:
    """Newest SpreadsheetML workbook in trade-portfolio inbox, then shared download dir."""
    paths = default_import_paths()
    if not paths:
        return None
    return max(paths, key=lambda p: os.path.getmtime(p))


def import_newest_trade_portfolio(
    *,
    sink: Sink = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """
    Parse the newest trade-portfolio export (inbox or ``data/input``) and upsert ``trade_portfolio_position``.
    Returns the upsert report dict, or ``None`` if no workbook was found.
    """
    path = resolve_default_trade_portfolio_path()
    if not path:
        _notify(
            "TRADE PORTFOLIO IMPORT: no SpreadsheetML workbook in trade portfolio inbox or data/input",
            sink,
        )
        return None
    _notify(f"TRADE PORTFOLIO IMPORT: {path}", sink)
    try:
        rep = import_trade_portfolio_file(path, db_path=db_path)
    except Exception:
        log.exception("TRADE PORTFOLIO IMPORT failed")
        raise
    _notify(
        "TRADE PORTFOLIO IMPORT: done — "
        f"snapshot {rep.get('snapshot_date')} portfolio {rep.get('portfolio_account')} "
        f"rows={rep.get('inserted')}",
        sink,
    )
    return rep
