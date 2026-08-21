"""Shared installment parse/group helpers + plan projection for the installments UI.

Used by ``ledger.store`` (statement_month fill) and ``api.installment_plans``.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

INSTALLMENT_PLAN_OVERRIDE_V19_DDL = """
CREATE TABLE IF NOT EXISTS installment_plan_override (
    plan_id     TEXT PRIMARY KEY,
    hidden      INTEGER NOT NULL DEFAULT 0,
    merged_into TEXT,
    split_by    TEXT CHECK (split_by IS NULL OR split_by IN ('last4', 'amount')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
) STRICT;
"""


def ensure_installment_plan_override_settled_column(conn: sqlite3.Connection) -> None:
    """v20: add ``settled`` if missing (idempotent)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='installment_plan_override'"
    ).fetchone()
    if not exists:
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(installment_plan_override)").fetchall()}
    if "settled" not in cols:
        conn.execute(
            "ALTER TABLE installment_plan_override ADD COLUMN settled INTEGER NOT NULL DEFAULT 0"
        )

INSTALLMENT_RE = re.compile(r"תשלום\s*(\d+)\s*מתוך\s*(\d+)")

AMOUNT_TOL_DEFAULT = 10.0
RECURRING_MIN_OCCURRENCES = 3
RECURRING_LAPSED_MONTHS = 2


@dataclass(frozen=True)
class InstallmentRow:
    id: int
    taarich: str
    bh: float
    source: str | None
    detail: str
    x: int
    y: int
    category: str | None = None
    last4: str | None = None
    statement_month: str | None = None
    is_parion: bool = False


def parse_installment_detail(detail: str) -> tuple[int, int] | None:
    if not detail or not detail.strip():
        return None
    m = INSTALLMENT_RE.search(detail)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def exclude_parion_installment(detail: str) -> bool:
    return "פרעון תשלום" in (detail or "")


def month_after_taarich_installment(taarich: str) -> str | None:
    try:
        d = date.fromisoformat(taarich.strip()[:10])
    except ValueError:
        return None
    y, m = d.year, d.month
    if m == 12:
        return f"{y + 1}-01"
    return f"{y:04d}-{m + 1:02d}"


def add_months_ym(ym: str, n: int) -> str:
    y, mo = map(int, ym.split("-"))
    idx = y * 12 + (mo - 1) + n
    y2, r = divmod(idx, 12)
    return f"{y2:04d}-{r + 1:02d}"


def months_between_ym(a: str, b: str) -> int:
    """Signed month distance ``b - a`` for ``YYYY-MM`` strings."""
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb * 12 + (mb - 1)) - (ya * 12 + (ma - 1))


def current_ym(today: date | None = None) -> str:
    d = today or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def cluster_by_amount(rows: list[InstallmentRow], max_span: float) -> list[list[InstallmentRow]]:
    if not rows:
        return []
    s = sorted(rows, key=lambda r: r.bh)
    out: list[list[InstallmentRow]] = []
    i = 0
    n = len(s)
    while i < n:
        j = i
        while j + 1 < n and s[j + 1].bh - s[i].bh <= max_span:
            j += 1
        out.append(s[i : j + 1])
        i = j + 1
    return out


def _median_amount(amounts: list[float]) -> float:
    if not amounts:
        return 0.0
    return float(statistics.median(amounts))


def _fmt_amt(v: float) -> str:
    return f"{v:.2f}"


def installment_plan_id(
    merchant: str | None,
    purchase_date: str,
    y: int,
    median_amount: float,
    *,
    card: str | None = None,
    exact_amount: float | None = None,
) -> str:
    mid = _fmt_amt(exact_amount if exact_amount is not None else median_amount)
    pid = f"inst:{merchant or ''}|{purchase_date}|{y}|{mid}"
    if card is not None:
        pid += f"|card:{card or ''}"
    return pid


def recurring_plan_id(merchant: str | None, median_amount: float, *, card: str | None = None) -> str:
    pid = f"rec:{merchant or ''}|{_fmt_amt(median_amount)}"
    if card is not None:
        pid += f"|card:{card or ''}"
    return pid



# Bank transfer extended text: keep recipient + account, drop free-form memo after.
_BANK_XFER_PREFIX_RE = re.compile(
    r"^(העברה אל:\s*.+?\s+\d{1,3}-\d{1,3}-\d+)",
    re.UNICODE,
)


def _normalize_extra_info(extended: str | None, detail: str | None) -> str:
    """Stable key from תאור מורחב / פירוט נוסף (memo after account number ignored)."""
    ext = (extended or "").strip()
    det = (detail or "").strip()
    raw = ext or det
    if not raw:
        return ""
    m = _BANK_XFER_PREFIX_RE.match(raw)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return re.sub(r"\s+", " ", raw).strip()


def _row_category(r: dict[str, Any]) -> str:
    cat = r.get("קטגוריה")
    if isinstance(cat, str):
        return cat.strip()
    return str(cat or "").strip()


def recurring_variable_plan_id(
    merchant: str | None,
    *,
    category: str = "",
    extra: str = "",
    card: str | None = None,
) -> str:
    """Variable-amount monthly: merchant + category + extra-info fingerprint."""
    dig = hashlib.sha1(f"{category}\0{extra}".encode("utf-8")).hexdigest()[:12]
    pid = f"rec:{merchant or ''}|var|{dig}"
    if card is not None:
        pid += f"|card:{card or ''}"
    return pid


def _passes_monthly_cadence(months: list[str]) -> bool:
    if len(months) < RECURRING_MIN_OCCURRENCES:
        return False
    gaps = [months_between_ym(months[i], months[i + 1]) for i in range(len(months) - 1)]
    if not gaps:
        return False
    med_gap = statistics.median(gaps)
    if 0.5 <= med_gap <= 1.5:
        return True
    ones = sum(1 for g in gaps if g == 1)
    twos = sum(1 for g in gaps if g == 2)
    if ones >= len(gaps) - 1:
        return True
    if ones + twos == len(gaps) and twos <= 1 and med_gap <= 2.0:
        return True
    return False


def plan_section(plan_id: str) -> Literal["installment", "recurring"] | None:
    if plan_id.startswith("inst:"):
        return "installment"
    if plan_id.startswith("rec:"):
        return "recurring"
    return None


def split_override_base_id(plan_id: str) -> str:
    """Strip ``|card:…`` so split_by sticks to the unsplit cluster id."""
    if "|card:" in plan_id:
        return plan_id.split("|card:")[0]
    return plan_id


def load_overrides(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    try:
        cur = conn.execute(
            "SELECT plan_id, hidden, merged_into, split_by, "
            "COALESCE(settled, 0) FROM installment_plan_override"
        )
    except sqlite3.OperationalError:
        # Pre-v20 / missing table
        try:
            cur = conn.execute(
                "SELECT plan_id, hidden, merged_into, split_by FROM installment_plan_override"
            )
        except sqlite3.OperationalError:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for plan_id, hidden, merged_into, split_by in cur.fetchall():
            out[str(plan_id)] = {
                "hidden": int(hidden or 0),
                "merged_into": merged_into,
                "split_by": split_by,
                "settled": 0,
            }
        return out
    out = {}
    for plan_id, hidden, merged_into, split_by, settled in cur.fetchall():
        out[str(plan_id)] = {
            "hidden": int(hidden or 0),
            "merged_into": merged_into,
            "split_by": split_by,
            "settled": int(settled or 0),
        }
    return out


def upsert_override(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    hidden: int | None = None,
    merged_into: str | None | object = ...,
    split_by: str | None | object = ...,
    settled: int | None = None,
) -> None:
    ensure_installment_plan_override_settled_column(conn)
    cur = conn.execute(
        "SELECT hidden, merged_into, split_by, COALESCE(settled, 0) "
        "FROM installment_plan_override WHERE plan_id = ?",
        (plan_id,),
    )
    row = cur.fetchone()
    if row:
        h, mi, sb, st = int(row[0] or 0), row[1], row[2], int(row[3] or 0)
    else:
        h, mi, sb, st = 0, None, None, 0
    if hidden is not None:
        h = 1 if hidden else 0
    if merged_into is not ...:
        mi = merged_into
    if split_by is not ...:
        sb = split_by
    if settled is not None:
        st = 1 if settled else 0
    conn.execute(
        """
        INSERT INTO installment_plan_override (plan_id, hidden, merged_into, split_by, settled, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(plan_id) DO UPDATE SET
            hidden = excluded.hidden,
            merged_into = excluded.merged_into,
            split_by = excluded.split_by,
            settled = excluded.settled,
            updated_at = excluded.updated_at
        """,
        (plan_id, h, mi, sb, st),
    )


@dataclass
class _RawCluster:
    """Pre-override installment cluster."""

    merchant: str | None
    purchase_date: str
    y: int
    rows: list[InstallmentRow]
    median_amount: float
    plan_id: str
    has_parion: bool = False


def _build_raw_installment_clusters(
    rows: list[InstallmentRow],
    *,
    amount_tol: float = AMOUNT_TOL_DEFAULT,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> list[_RawCluster]:
    """Group tagged installment rows; apply split_by overrides when present."""
    overrides = overrides or {}
    buckets: dict[tuple[str | None, str, int], list[InstallmentRow]] = {}
    parion_keys: set[tuple[str | None, str]] = set()

    for row in rows:
        if row.is_parion:
            parion_keys.add((row.source, row.taarich))
            continue
        key = (row.source, row.taarich, row.y)
        buckets.setdefault(key, []).append(row)

    clusters: list[_RawCluster] = []
    for (src, ta, y), bucket in buckets.items():
        if month_after_taarich_installment(ta) is None:
            continue
        has_parion = (src, ta) in parion_keys
        for amt_cluster in cluster_by_amount(bucket, amount_tol):
            med = _median_amount([r.bh for r in amt_cluster])
            base_id = installment_plan_id(src, ta, y, med)
            split_by = (overrides.get(base_id) or {}).get("split_by")

            if split_by == "last4":
                by_card: dict[str, list[InstallmentRow]] = {}
                for r in amt_cluster:
                    by_card.setdefault(r.last4 or "", []).append(r)
                for card, croes in by_card.items():
                    cmed = _median_amount([r.bh for r in croes])
                    clusters.append(
                        _RawCluster(
                            merchant=src,
                            purchase_date=ta,
                            y=y,
                            rows=croes,
                            median_amount=cmed,
                            plan_id=installment_plan_id(src, ta, y, cmed, card=card),
                            has_parion=has_parion,
                        )
                    )
            elif split_by == "amount":
                by_amt: dict[str, list[InstallmentRow]] = {}
                for r in amt_cluster:
                    by_amt.setdefault(_fmt_amt(r.bh), []).append(r)
                for amt_key, arows in by_amt.items():
                    av = float(amt_key)
                    clusters.append(
                        _RawCluster(
                            merchant=src,
                            purchase_date=ta,
                            y=y,
                            rows=arows,
                            median_amount=av,
                            plan_id=installment_plan_id(src, ta, y, av, exact_amount=av),
                            has_parion=has_parion,
                        )
                    )
            else:
                clusters.append(
                    _RawCluster(
                        merchant=src,
                        purchase_date=ta,
                        y=y,
                        rows=amt_cluster,
                        median_amount=med,
                        plan_id=base_id,
                        has_parion=has_parion,
                    )
                )
    return clusters


def project_installment_plan(
    cluster: _RawCluster,
    *,
    as_of: str,
    hidden: bool = False,
    settled: bool = False,
    ledger_cutoff_ym: str | None = None,
) -> dict[str, Any]:
    """
    Project a finite installment plan.

    Missing payments *before* the plan's first observed installment (or before
    ``ledger_cutoff_ym``) are treated as a data cutoff, not gaps. Gaps only
    alert when a payment is missing *after* that starting point along the series.
    Transactions themselves are never dropped.

    ``settled`` marks remaining installments as paid off manually (early payoff /
    resolved outside the ledger).
    """
    by_x: dict[int, InstallmentRow] = {}
    for r in cluster.rows:
        by_x.setdefault(r.x, r)

    y = cluster.y
    base = month_after_taarich_installment(cluster.purchase_date)
    assert base is not None
    last_month = add_months_ym(base, y - 1)
    observed_xs = set(by_x.keys())
    med = cluster.median_amount
    x_first = min(observed_xs) if observed_xs else None
    first_obs_month = add_months_ym(base, x_first - 1) if x_first is not None else None

    cutoff_candidates = [c for c in (first_obs_month, ledger_cutoff_ym) if c]
    cutoff_month = max(cutoff_candidates) if cutoff_candidates else None

    timeline: list[dict[str, Any]] = []
    missing_along_line = False
    next_month: str | None = None
    due_this_month = 0.0
    remaining_xs: list[int] = []

    for i in range(1, y + 1):
        month = add_months_ym(base, i - 1)
        row = by_x.get(i)
        if row is not None:
            state = "observed"
            amount: float | None = row.bh
            if month == as_of and not settled:
                due_this_month += row.bh
        else:
            before_cutoff = False
            if x_first is not None and i < x_first:
                before_cutoff = True
            if ledger_cutoff_ym and month < ledger_cutoff_ym:
                before_cutoff = True

            if before_cutoff:
                state = "before_cutoff"
                amount = med
            elif settled:
                # Manual "paid off the rest" — not remaining, not a gap
                state = "settled"
                amount = med
            elif month < as_of:
                state = "missing"
                missing_along_line = True
                amount = med
                remaining_xs.append(i)
            else:
                state = "expected"
                amount = med
                remaining_xs.append(i)
                if month == as_of:
                    due_this_month += med
                if next_month is None:
                    next_month = month

        timeline.append({"month": month, "x": i, "amount": amount, "state": state})

    if next_month is None and not settled:
        for t in timeline:
            if t["state"] == "expected":
                next_month = t["month"]
                break
        if next_month is None:
            for t in timeline:
                if t["state"] == "missing":
                    next_month = t["month"]
                    break

    remaining_ils = round(len(remaining_xs) * med, 2)

    if cluster.has_parion or settled:
        status = "paid_off"
        remaining_xs = []
        remaining_ils = 0.0
        next_month = None
        due_this_month = 0.0
    elif not remaining_xs:
        status = "completed"
        next_month = None
    elif missing_along_line:
        status = "gapped"
    else:
        status = "active"

    cats = sorted({(r.category or "").strip() for r in cluster.rows if (r.category or "").strip()})
    cards = sorted({(r.last4 or "").strip() for r in cluster.rows if (r.last4 or "").strip()})

    return {
        "plan_id": cluster.plan_id,
        "merchant": cluster.merchant or "",
        "category": cats[0] if len(cats) == 1 else (cats[0] if cats else ""),
        "categories": cats,
        "amount": med,
        "paid_count": len(observed_xs),
        "total_count": y,
        "remaining_count": len(remaining_xs),
        "remaining_ils": remaining_ils,
        "next_month": next_month,
        "last_month": last_month,
        "purchase_date": cluster.purchase_date,
        "cutoff_month": cutoff_month,
        "first_observed_x": x_first,
        "last4s": cards,
        "status": status,
        "hidden": hidden,
        "settled": settled,
        "due_this_month": round(due_this_month, 2),
        "timeline": timeline,
        "tx_ids": [r.id for r in cluster.rows],
    }


@dataclass
class _RecurringCluster:
    merchant: str | None
    rows: list[dict[str, Any]]
    median_amount: float
    plan_id: str
    amount_mode: str = "fixed"  # fixed | variable
    category: str = ""
    extra_label: str = ""


def _ym_from_row(r: dict[str, Any]) -> str | None:
    sm = r.get("statement_month")
    if sm and str(sm).strip():
        s = str(sm).strip()[:7]
        if len(s) == 7 and s[4] == "-":
            return s
    ta = str(r.get("תאריך") or "").strip()[:10]
    if len(ta) >= 7:
        return ta[:7]
    return None


def _build_raw_recurring(
    expense_rows: list[dict[str, Any]],
    *,
    amount_tol: float = AMOUNT_TOL_DEFAULT,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> list[_RecurringCluster]:
    overrides = overrides or {}
    candidates: list[dict[str, Any]] = []
    for r in expense_rows:
        detail = str(r.get("פירוט נוסף") or "")
        if parse_installment_detail(detail) or exclude_parion_installment(detail):
            continue
        try:
            bh = float(r.get("בחובה") or 0)
        except (TypeError, ValueError):
            continue
        if bh <= 0:
            continue
        src = r.get("מקור עסקה")
        if isinstance(src, str):
            src = src.strip() or None
        else:
            src = None
        if not src:
            continue
        ym = _ym_from_row(r)
        if not ym:
            continue
        cat = _row_category(r)
        extra = _normalize_extra_info(
            str(r.get("תאור מורחב") or "") or None,
            detail or None,
        )
        candidates.append(
            {**r, "_bh": bh, "_src": src, "_ym": ym, "_cat": cat, "_extra": extra}
        )

    by_merchant: dict[str, list[dict[str, Any]]] = {}
    for r in candidates:
        by_merchant.setdefault(r["_src"], []).append(r)

    out: list[_RecurringCluster] = []
    covered_row_ids: set[int] = set()

    def _append_fixed(
        cluster_rows: list[dict[str, Any]],
        plan_id: str,
        merchant: str,
        *,
        category: str,
    ) -> None:
        med = _median_amount([r["_bh"] for r in cluster_rows])
        out.append(
            _RecurringCluster(
                merchant=merchant,
                rows=cluster_rows,
                median_amount=med,
                plan_id=plan_id,
                amount_mode="fixed",
                category=category,
                extra_label="",
            )
        )
        for r in cluster_rows:
            if r.get("id") is not None:
                covered_row_ids.add(int(r["id"]))

    for merchant, mrows in by_merchant.items():
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for r in mrows:
            by_cat.setdefault(str(r.get("_cat") or ""), []).append(r)

        for cat, cat_rows in by_cat.items():
            for amt_cluster in _cluster_dicts_by_amount(cat_rows, amount_tol):
                months = sorted({r["_ym"] for r in amt_cluster})
                if not _passes_monthly_cadence(months):
                    continue

                med = _median_amount([r["_bh"] for r in amt_cluster])
                base_id = recurring_plan_id(merchant, med)
                split_by = (overrides.get(base_id) or {}).get("split_by")

                if split_by == "last4":
                    by_card: dict[str, list[dict[str, Any]]] = {}
                    for r in amt_cluster:
                        card = str(r.get("4 ספרות") or "").strip()
                        by_card.setdefault(card, []).append(r)
                    for card, croes in by_card.items():
                        if not _passes_monthly_cadence(sorted({r["_ym"] for r in croes})):
                            continue
                        cmed = _median_amount([r["_bh"] for r in croes])
                        _append_fixed(
                            croes,
                            recurring_plan_id(merchant, cmed, card=card),
                            merchant,
                            category=cat,
                        )
                elif split_by == "amount":
                    by_amt: dict[str, list[dict[str, Any]]] = {}
                    for r in amt_cluster:
                        by_amt.setdefault(_fmt_amt(r["_bh"]), []).append(r)
                    for amt_key, arows in by_amt.items():
                        if not _passes_monthly_cadence(sorted({r["_ym"] for r in arows})):
                            continue
                        av = float(amt_key)
                        _append_fixed(
                            arows,
                            recurring_plan_id(merchant, av),
                            merchant,
                            category=cat,
                        )
                else:
                    _append_fixed(amt_cluster, base_id, merchant, category=cat)

    # Variable-amount monthly: merchant + category + extra info (not merchant alone).
    by_var_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in candidates:
        if r.get("id") is not None and int(r["id"]) in covered_row_ids:
            continue
        key = (str(r["_src"]), str(r.get("_cat") or ""), str(r.get("_extra") or ""))
        by_var_key.setdefault(key, []).append(r)

    for (merchant, cat, extra), rows_for_var in by_var_key.items():
        months = sorted({r["_ym"] for r in rows_for_var})
        if not _passes_monthly_cadence(months):
            continue
        base_id = recurring_variable_plan_id(merchant, category=cat, extra=extra)
        split_by = (overrides.get(base_id) or {}).get("split_by")
        med = _median_amount([r["_bh"] for r in rows_for_var])

        if split_by == "last4":
            by_card: dict[str, list[dict[str, Any]]] = {}
            for r in rows_for_var:
                card = str(r.get("4 ספרות") or "").strip()
                by_card.setdefault(card, []).append(r)
            for card, croes in by_card.items():
                if not _passes_monthly_cadence(sorted({r["_ym"] for r in croes})):
                    continue
                cmed = _median_amount([r["_bh"] for r in croes])
                out.append(
                    _RecurringCluster(
                        merchant=merchant,
                        rows=croes,
                        median_amount=cmed,
                        plan_id=recurring_variable_plan_id(
                            merchant, category=cat, extra=extra, card=card
                        ),
                        amount_mode="variable",
                        category=cat,
                        extra_label=extra,
                    )
                )
        else:
            out.append(
                _RecurringCluster(
                    merchant=merchant,
                    rows=rows_for_var,
                    median_amount=med,
                    plan_id=base_id,
                    amount_mode="variable",
                    category=cat,
                    extra_label=extra,
                )
            )
    return out


def _cluster_dicts_by_amount(rows: list[dict[str, Any]], max_span: float) -> list[list[dict[str, Any]]]:
    if not rows:
        return []
    s = sorted(rows, key=lambda r: r["_bh"])
    out: list[list[dict[str, Any]]] = []
    i = 0
    n = len(s)
    while i < n:
        j = i
        while j + 1 < n and s[j + 1]["_bh"] - s[i]["_bh"] <= max_span:
            j += 1
        out.append(s[i : j + 1])
        i = j + 1
    return out


def project_recurring_fee(
    cluster: _RecurringCluster,
    *,
    as_of: str,
    hidden: bool = False,
) -> dict[str, Any]:
    by_month: dict[str, float] = {}
    for r in cluster.rows:
        ym = r.get("_ym") or _ym_from_row(r)
        if not ym:
            continue
        try:
            bh = float(r.get("_bh") if "_bh" in r else r.get("בחובה") or 0)
        except (TypeError, ValueError):
            continue
        by_month[ym] = by_month.get(ym, 0.0) + bh

    months = sorted(by_month.keys())
    if not months:
        months = sorted({r["_ym"] for r in cluster.rows if r.get("_ym")})
    first_seen = months[0] if months else ""
    last_seen = months[-1] if months else ""
    next_expected = add_months_ym(last_seen, 1) if last_seen else None
    lag = months_between_ym(last_seen, as_of) if last_seen else RECURRING_LAPSED_MONTHS + 1
    status = "lapsed" if lag > RECURRING_LAPSED_MONTHS else "active"
    lifetime = round(
        sum(by_month.values()) if by_month else sum(float(r.get("_bh") or 0) for r in cluster.rows),
        2,
    )
    month_totals = list(by_month.values())
    # Present-looking figure: total charged in the latest seen month (not historical median).
    if last_seen and last_seen in by_month:
        monthly = float(by_month[last_seen])
    elif month_totals:
        monthly = float(month_totals[-1])
    else:
        monthly = float(cluster.median_amount)
    last_month_total = round(monthly, 2)
    typical_monthly = (
        round(_median_amount(month_totals), 2) if month_totals else round(monthly, 2)
    )
    cats = sorted(
        {
            str(r.get("קטגוריה") or "").strip()
            for r in cluster.rows
            if str(r.get("קטגוריה") or "").strip()
        }
    )
    cards = sorted(
        {
            str(r.get("4 ספרות") or "").strip()
            for r in cluster.rows
            if str(r.get("4 ספרות") or "").strip()
        }
    )
    amount_mode = cluster.amount_mode
    if "|var|" in cluster.plan_id or cluster.plan_id.endswith("|var"):
        amount_mode = "variable"
    category = cluster.category or (cats[0] if cats else "")
    extra_label = cluster.extra_label
    if not extra_label and amount_mode == "variable":
        extras = {
            _normalize_extra_info(
                str(r.get("תאור מורחב") or "") or None,
                str(r.get("פירוט נוסף") or "") or None,
            )
            for r in cluster.rows
        }
        extras.discard("")
        if len(extras) == 1:
            extra_label = next(iter(extras))
    return {
        "plan_id": cluster.plan_id,
        "merchant": cluster.merchant or "",
        "category": category,
        "categories": cats,
        "extra_label": extra_label,
        "amount_mode": amount_mode,
        "monthly_amount": round(monthly, 2),
        "last_month_amount": last_month_total,
        "typical_monthly_amount": typical_monthly,
        "months_seen": len(months),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "next_expected": next_expected if status == "active" else None,
        "lifetime_paid": lifetime,
        "last4s": cards,
        "status": status,
        "hidden": hidden,
        "tx_ids": [int(r["id"]) for r in cluster.rows if r.get("id") is not None],
        "_rows": cluster.rows,
    }



def _canonical_merge_target(
    plan_id: str,
    overrides: dict[str, dict[str, Any]],
    *,
    section: Literal["installment", "recurring"],
) -> str:
    """Follow merged_into links to the root plan (cycle-safe)."""
    seen: set[str] = set()
    cur = plan_id
    while True:
        if cur in seen:
            return cur
        seen.add(cur)
        ov = overrides.get(cur) or {}
        nxt = ov.get("merged_into")
        if not nxt or not isinstance(nxt, str) or not nxt.strip():
            return cur
        nxt = nxt.strip()
        if plan_section(nxt) != section:
            return cur
        cur = nxt


def _merged_source_summaries(
    members: list[dict[str, Any]],
    root: str,
    overrides: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Plans absorbed into ``root`` (for UI unmerge)."""
    sources: list[dict[str, Any]] = []
    for m in members:
        pid = m.get("plan_id")
        if not pid or pid == root:
            continue
        direct = (overrides.get(str(pid)) or {}).get("merged_into")
        entry: dict[str, Any] = {
            "plan_id": pid,
            "merchant": m.get("merchant") or "",
            "category": m.get("category") or "",
            "merged_into": direct if isinstance(direct, str) and direct.strip() else root,
        }
        if "monthly_amount" in m:
            entry["monthly_amount"] = m.get("monthly_amount")
            entry["amount_mode"] = m.get("amount_mode") or "fixed"
        if "amount" in m:
            entry["amount"] = m.get("amount")
        if "status" in m:
            entry["status"] = m.get("status")
        sources.append(entry)
    return sources


def _resolve_merges(
    plans: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
    *,
    section: Literal["installment", "recurring"],
    as_of: str,
) -> list[dict[str, Any]]:
    """Apply merged_into (transitive). Recurring fees are fully re-projected from unioned rows."""
    by_id = {p["plan_id"]: p for p in plans}
    if not by_id:
        return plans

    # Group members under their canonical root
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in plans:
        pid = p["plan_id"]
        if plan_section(pid) != section:
            continue
        root = _canonical_merge_target(pid, overrides, section=section)
        if root not in by_id:
            # Dangling merge target — keep plan as-is
            groups.setdefault(pid, []).append(p)
            continue
        groups.setdefault(root, []).append(p)

    out: list[dict[str, Any]] = []
    emitted: set[str] = set()

    for root, members in groups.items():
        if root in emitted:
            continue
        if len(members) == 1 and members[0]["plan_id"] == root:
            out.append(members[0])
            emitted.add(root)
            continue

        sources = _merged_source_summaries(members, root, overrides)

        if section == "recurring":
            rows: list[dict[str, Any]] = []
            seen_ids: set[int] = set()
            for m in members:
                for r in m.get("_rows") or []:
                    rid = r.get("id")
                    if rid is not None:
                        ir = int(rid)
                        if ir in seen_ids:
                            continue
                        seen_ids.add(ir)
                    rows.append(r)
            root_plan = by_id[root]
            modes = {str(m.get("amount_mode") or "fixed") for m in members}
            amount_mode = "variable" if "variable" in modes or "|var" in root else "fixed"
            extras = {str(m.get("extra_label") or "") for m in members}
            extras.discard("")
            cluster = _RecurringCluster(
                merchant=root_plan.get("merchant") or None,
                rows=rows,
                median_amount=float(root_plan.get("monthly_amount") or 0),
                plan_id=root,
                amount_mode=amount_mode,
                category=str(root_plan.get("category") or ""),
                extra_label=(next(iter(extras)) if len(extras) == 1 else ""),
            )
            merged = project_recurring_fee(
                cluster,
                as_of=as_of,
                hidden=bool((overrides.get(root) or {}).get("hidden")),
            )
            if sources:
                merged["merged_sources"] = sources
            out.append(merged)
            emitted.add(root)
            continue

        # Installments: sum leftover / due; union tx_ids (shallow — rare to merge finite plans)
        dst = dict(by_id[root])
        for src in members:
            if src["plan_id"] == root:
                continue
            dst_ids = set(dst.get("tx_ids") or [])
            for tid in src.get("tx_ids") or []:
                if tid not in dst_ids:
                    (dst.setdefault("tx_ids", [])).append(tid)
                    dst_ids.add(tid)
            dst["remaining_ils"] = round(
                float(dst.get("remaining_ils") or 0) + float(src.get("remaining_ils") or 0), 2
            )
            dst["due_this_month"] = round(
                float(dst.get("due_this_month") or 0) + float(src.get("due_this_month") or 0), 2
            )
            dst["paid_count"] = int(dst.get("paid_count") or 0) + int(src.get("paid_count") or 0)
            dst["last4s"] = sorted(set(dst.get("last4s") or []) | set(src.get("last4s") or []))
        if sources:
            dst["merged_sources"] = sources
        out.append(dst)
        emitted.add(root)

    # Include any plan that wasn't grouped (shouldn't happen)
    for p in plans:
        if p["plan_id"] not in emitted and plan_section(p["plan_id"]) == section:
            # Absorbed into another root
            root = _canonical_merge_target(p["plan_id"], overrides, section=section)
            if root != p["plan_id"] and root in emitted:
                continue
            out.append(p)

    return out


def _strip_internal_plan_fields(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for p in plans:
        cleaned.append({k: v for k, v in p.items() if not str(k).startswith("_")})
    return cleaned


def build_plans_payload(
    conn: sqlite3.Connection,
    *,
    include_hidden: bool = False,
    q: str | None = None,
    as_of: str | None = None,
    amount_tol: float = AMOUNT_TOL_DEFAULT,
) -> dict[str, Any]:
    # Keep in sync with ledger.store.LEDGER_SQL_TX_INCLUDED (avoid import cycle).
    included = "(COALESCE(excluded_from_calculations, 0) = 0)"

    as_of_ym = as_of or current_ym()
    overrides = load_overrides(conn)

    cur = conn.execute(
        f"""
        SELECT id, "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף",
               "תאור מורחב", "קטגוריה", "4 ספרות", statement_month
        FROM ledger_transaction
        WHERE {included}
          AND "תאריך" IS NOT NULL
          AND TRIM("תאריך") != ''
        """
    )
    cols = [d[0] for d in cur.description]
    all_rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    coverage_months = [ym for r in all_rows if (ym := _ym_from_row(r))]
    ledger_cutoff_ym = min(coverage_months) if coverage_months else None

    inst_rows: list[InstallmentRow] = []
    expense_rows: list[dict[str, Any]] = []
    for r in all_rows:
        detail = str(r.get("פירוט נוסף") or "")
        src = r.get("מקור עסקה")
        if isinstance(src, str):
            src = src.strip() or None
        else:
            src = None
        cat = r.get("קטגוריה")
        if isinstance(cat, str):
            cat = cat.strip() or None
        last4 = r.get("4 ספרות")
        if isinstance(last4, str):
            last4 = last4.strip() or None
        else:
            last4 = None
        ta = str(r.get("תאריך") or "").strip()[:10]
        is_parion = exclude_parion_installment(detail)
        inst = parse_installment_detail(detail)
        try:
            bh = float(r.get("בחובה") or 0)
        except (TypeError, ValueError):
            bh = 0.0

        if is_parion or inst:
            x, y = (0, 0) if is_parion else inst  # type: ignore[misc]
            if not is_parion:
                assert inst is not None
                x, y = inst
                if y < 1 or x < 1 or x > y:
                    expense_rows.append(r)
                    continue
            inst_rows.append(
                InstallmentRow(
                    id=int(r["id"]),
                    taarich=ta,
                    bh=bh,
                    source=src,
                    detail=detail,
                    x=x,
                    y=y if not is_parion else 0,
                    category=cat,
                    last4=last4,
                    statement_month=(str(r["statement_month"]).strip() if r.get("statement_month") else None),
                    is_parion=is_parion,
                )
            )
        else:
            expense_rows.append(r)

    q_norm = (q or "").strip().lower()

    raw_inst = _build_raw_installment_clusters(inst_rows, amount_tol=amount_tol, overrides=overrides)
    inst_plans: list[dict[str, Any]] = []
    for cl in raw_inst:
        ov = overrides.get(cl.plan_id) or {}
        hidden = bool(ov.get("hidden"))
        if hidden and not include_hidden:
            continue
        plan = project_installment_plan(
            cl,
            as_of=as_of_ym,
            hidden=hidden,
            settled=bool(ov.get("settled")),
            ledger_cutoff_ym=ledger_cutoff_ym,
        )
        if q_norm and q_norm not in (plan.get("merchant") or "").lower():
            continue
        inst_plans.append(plan)

    inst_plans = _resolve_merges(
        inst_plans, overrides, section="installment", as_of=as_of_ym
    )
    # Re-apply hide after merge (merged targets may stay)
    if not include_hidden:
        inst_plans = [
            p for p in inst_plans if not (overrides.get(p["plan_id"]) or {}).get("hidden")
        ]

    raw_rec = _build_raw_recurring(expense_rows, amount_tol=amount_tol, overrides=overrides)
    rec_fees: list[dict[str, Any]] = []
    for cl in raw_rec:
        ov = overrides.get(cl.plan_id) or {}
        hidden = bool(ov.get("hidden"))
        if hidden and not include_hidden:
            continue
        fee = project_recurring_fee(cl, as_of=as_of_ym, hidden=hidden)
        if q_norm and q_norm not in (fee.get("merchant") or "").lower():
            continue
        rec_fees.append(fee)

    # Include merge sources even if amount-clustered away from the q list? Already same merchant.
    # Bring in any override-linked siblings missing from the filtered list so merges stay correct.
    needed_ids: set[str] = set()
    for f in rec_fees:
        root = _canonical_merge_target(f["plan_id"], overrides, section="recurring")
        needed_ids.add(root)
        for pid, ov in overrides.items():
            if plan_section(pid) != "recurring":
                continue
            if _canonical_merge_target(pid, overrides, section="recurring") == root:
                needed_ids.add(pid)
    have = {f["plan_id"] for f in rec_fees}
    if needed_ids - have:
        by_raw = {cl.plan_id: cl for cl in raw_rec}
        for pid in needed_ids - have:
            cl = by_raw.get(pid)
            if cl is None:
                continue
            ov = overrides.get(pid) or {}
            if bool(ov.get("hidden")) and not include_hidden:
                # Still include hidden sources into merge math, then strip if root hidden
                pass
            fee = project_recurring_fee(
                cl, as_of=as_of_ym, hidden=bool(ov.get("hidden"))
            )
            rec_fees.append(fee)

    rec_fees = _resolve_merges(
        rec_fees, overrides, section="recurring", as_of=as_of_ym
    )
    if not include_hidden:
        rec_fees = [
            p for p in rec_fees if not (overrides.get(p["plan_id"]) or {}).get("hidden")
        ]

    inst_plans = _strip_internal_plan_fields(inst_plans)
    rec_fees = _strip_internal_plan_fields(rec_fees)

    # Sort: active first, then by next/last
    def _inst_sort(p: dict[str, Any]) -> tuple:
        status_rank = {"gapped": 0, "active": 1, "paid_off": 2, "completed": 3}.get(p.get("status"), 9)
        return (status_rank, p.get("next_month") or "9999-99", p.get("merchant") or "")

    def _rec_sort(p: dict[str, Any]) -> tuple:
        status_rank = {"active": 0, "lapsed": 1}.get(p.get("status"), 9)
        return (status_rank, -(p.get("monthly_amount") or 0), p.get("merchant") or "")

    inst_plans.sort(key=_inst_sort)
    rec_fees.sort(key=_rec_sort)

    active_inst = [p for p in inst_plans if p.get("status") in ("active", "gapped")]
    due_this = round(sum(float(p.get("due_this_month") or 0) for p in active_inst), 2)
    remaining_total = round(sum(float(p.get("remaining_ils") or 0) for p in active_inst), 2)
    last_payments = sorted(
        p.get("last_month") for p in active_inst if p.get("last_month")
    )
    nearest_last = last_payments[0] if last_payments else None

    active_rec = [p for p in rec_fees if p.get("status") == "active"]
    lapsed_rec = [p for p in rec_fees if p.get("status") == "lapsed"]
    monthly_burn = round(sum(float(p.get("monthly_amount") or 0) for p in active_rec), 2)
    variable_active = sum(1 for p in active_rec if p.get("amount_mode") == "variable")
    top_active = sorted(
        [
            {
                "plan_id": p["plan_id"],
                "merchant": p.get("merchant") or "",
                "monthly_amount": float(p.get("monthly_amount") or 0),
                "amount_mode": p.get("amount_mode") or "fixed",
                "category": p.get("category") or "",
            }
            for p in active_rec
        ],
        key=lambda x: x["monthly_amount"],
        reverse=True,
    )[:12]

    return {
        "ok": True,
        "as_of": as_of_ym,
        "ledger_cutoff": ledger_cutoff_ym,
        "installments": {
            "summary": {
                "active": len(active_inst),
                "due_this_month": due_this,
                "remaining_total": remaining_total,
                "nearest_last_payment": nearest_last,
            },
            "plans": inst_plans,
        },
        "recurring": {
            "summary": {
                "active": len(active_rec),
                "monthly_burn": monthly_burn,
                "lapsed": len(lapsed_rec),
                "variable_active": variable_active,
                "top_active": top_active,
            },
            "fees": rec_fees,
        },
    }
