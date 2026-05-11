"""
Heatmap data for /heatmap: pivots, paint matrices, summary stats as JSON.

Rendering (colors, money formatting, stats cell styling) lives in the React SPA.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

import config

log = logging.getLogger(__name__)

ReportType = Literal["expense", "income", "net"]

_bundle_cache: dict[str, Any] = {"path": "", "mtime": 0.0, "bundle": None}


def invalidate_bundle_cache() -> None:
    _bundle_cache["path"] = ""
    _bundle_cache["mtime"] = 0.0
    _bundle_cache["bundle"] = None


# Heatmap-only: normalize mixed sheet/CSV date strings for bucketing (ISO YYYY-MM-DD first,
# then D/M/Y, then pandas with dayfirst=True — same contract as fingerprint / compile). Does not affect compile or other pipelines.
_HEATMAP_ISO_DATE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](?:\d{2}:\d{2}(?::\d{2})?)?(?:\.\d+)?)?"
)
_HEATMAP_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?:\b|[ T].*)?$")


def _heatmap_parse_one_date(val: Any) -> pd.Timestamp:
    if val is None or val is pd.NaT:
        return pd.NaT
    if isinstance(val, pd.Timestamp):
        return val.normalize()
    if isinstance(val, datetime):
        return pd.Timestamp(val).normalize()
    if isinstance(val, date):
        return pd.Timestamp(val)
    if isinstance(val, (float, np.floating)) and np.isnan(val):
        return pd.NaT
    if isinstance(val, (np.datetime64,)):
        return pd.Timestamp(val).normalize()

    s = str(val).strip()
    if not s or s.lower() in ("nan", "nat", "none", "<na>"):
        return pd.NaT

    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        n = float(s)
        if abs(n - round(n)) < 1e-9 and 20000 < n < 120000:
            return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(round(n)))).normalize()

    m = _HEATMAP_ISO_DATE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return pd.Timestamp(year=y, month=mo, day=d)
        except ValueError:
            return pd.NaT

    m = _HEATMAP_DMY.match(s)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y_raw = m.group(3)
        y = int(y_raw)
        if len(y_raw) == 2:
            y += 2000 if y < 70 else 1900
        try:
            return pd.Timestamp(year=y, month=mo, day=d)
        except ValueError:
            pass

    ts = pd.to_datetime(s, dayfirst=True, format="mixed", errors="coerce")
    return ts.normalize() if pd.notna(ts) else pd.NaT


def _heatmap_parse_dates(series: pd.Series) -> pd.Series:
    out = series.map(_heatmap_parse_one_date)
    if not isinstance(out, pd.Series):
        out = pd.Series(out, index=series.index)
    return pd.to_datetime(out, errors="coerce", dayfirst=True)


def _heatmap_effective_year_month(df: pd.DataFrame) -> pd.Series:
    """Month bucket for pivots: prefer ``statement_month`` (YYYY-MM) when set and valid; else ``תאריך``."""
    ta = df["תאריך"]
    ym_from_date = pd.Series(pd.NA, index=df.index, dtype="string")
    has_t = ta.notna()
    ym_from_date.loc[has_t] = ta.loc[has_t].dt.strftime("%Y-%m")

    if "statement_month" not in df.columns:
        return ym_from_date

    sm = df["statement_month"]
    sm_str = sm.map(lambda x: "" if pd.isna(x) else str(x).strip())
    valid_sm = sm_str.str.fullmatch(r"\d{4}-\d{2}", na=False)
    out = ym_from_date.copy()
    out.loc[valid_sm] = sm_str.loc[valid_sm]
    return out


def _heatmap_effective_tx_date(df: pd.DataFrame) -> pd.Series:
    """Calendar day for period filters / detail sort: first of valid ``statement_month``, else ``תאריך``."""
    ta = df["תאריך"]
    out = ta.copy()
    if "statement_month" not in df.columns:
        return out
    sm = df["statement_month"]
    sm_str = sm.map(lambda x: "" if pd.isna(x) else str(x).strip())
    valid_sm = sm_str.str.fullmatch(r"\d{4}-\d{2}", na=False)
    first_days = pd.to_datetime(sm_str + "-01", format="%Y-%m-%d", errors="coerce")
    out.loc[valid_sm] = first_days.loc[valid_sm]
    return out


# Category column "average": mean over up to this many most recent *active* months (non-zero cells),
# scanning from newest month downward — avoids dilution from long runs of zero-activity months.
_HEATMAP_CATEGORY_MEAN_ACTIVE_MONTHS = 12


def _heatmap_category_cell_is_active(value: float, report_type: ReportType) -> bool:
    if not np.isfinite(value):
        return False
    if report_type == "net":
        return value != 0.0
    return value > 0.0


def category_mean_recent_active(col: pd.Series, report_type: ReportType) -> float:
    """Mean of values in up to the last N months with activity (N ≤ 12), newest-first index order."""
    cap = int(_HEATMAP_CATEGORY_MEAN_ACTIVE_MONTHS)
    if cap < 1:
        cap = 1
    picked: list[float] = []
    for v in col.astype(float):
        if _heatmap_category_cell_is_active(float(v), report_type):
            picked.append(float(v))
            if len(picked) >= cap:
                break
    if not picked:
        return 0.0
    return float(np.mean(picked))


def _pivot_mean_stat(p: pd.DataFrame, axis: int, report_type: ReportType) -> pd.Series:
    """Row axis = mean across categories (unchanged). Column axis = recent-active category mean."""
    if axis == 1:
        return p.mean(axis=1)
    return pd.Series({c: category_mean_recent_active(p[c], report_type) for c in p.columns})


STAT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "total": {
        "name": "סך הכל (Total)",
        "func": lambda p, axis, rt: p.sum(axis=axis),
    },
    "mean": {
        "name_by_cat": "ממוצע חודשי (Avg)",
        "name_by_month": "ממוצע לקטגוריה (Avg)",
        "func": _pivot_mean_stat,
    },
    "std": {
        "name": "סטיית תקן (Std Dev)",
        "func": lambda p, axis, rt: p.std(axis=axis),
    },
    "median": {
        "name": "חציון (Median)",
        "func": lambda p, axis, rt: p.median(axis=axis),
    },
    "max": {
        "name": "מקסימום (Max)",
        "func": lambda p, axis, rt: p.max(axis=axis),
    },
    "min": {
        "name": "מינימום (Min)",
        "func": lambda p, axis, rt: p[p > 0].min(axis=axis)
        if rt in ("expense", "income")
        else p.min(axis=axis),
    },
    "p25": {
        "name": "אחוזון 25 (25th Pctl)",
        "func": lambda p, axis, rt: p.quantile(0.25, axis=axis),
    },
    "p75": {
        "name": "אחוזון 75 (75th Pctl)",
        "func": lambda p, axis, rt: p.quantile(0.75, axis=axis),
    },
    "count": {
        "name": "ספירה (Count > 0)",
        "func": lambda p, axis, rt: (p != 0).sum(axis=axis),
    },
}

DESIRED_STATS = ["total", "mean", "median"]

# Column order: blend recent window vs all-time (min–max per metric across categories).
_RECENCY_MONTHS = 5
_RECENCY_WEIGHT = 0.55  # 1.0 = only last-N months; 0.0 = only all-time


def _min_max_norm_series(s: pd.Series) -> pd.Series:
    lo, hi = float(s.min()), float(s.max())
    if hi <= lo or not np.isfinite(lo) or not np.isfinite(hi):
        return pd.Series(0.0, index=s.index, dtype=float)
    return (s.astype(float) - lo) / (hi - lo)


def _reorder_pivot_columns_recency_weighted(pivot: pd.DataFrame) -> pd.DataFrame:
    """Sort columns by recency: recent activity + all-time activity, normalized across categories.

    Rows are newest-first (``YearMonth`` descending). The last ``_RECENCY_MONTHS`` rows are the
    "recent" window. Each category gets ``log1p`` total abs activity for recent and all-time,
    min–max normalized across columns, then blended. Lower score → left, higher → right.
    """
    if pivot.empty or pivot.shape[1] == 0:
        return pivot
    n = len(pivot.index)
    k = min(_RECENCY_MONTHS, n)
    recent_sum = pivot.iloc[:k].abs().sum(axis=0).astype(float)
    alltime_sum = pivot.abs().sum(axis=0).astype(float)
    r = np.log1p(recent_sum)
    a = np.log1p(alltime_sum)
    r_n = _min_max_norm_series(r)
    a_n = _min_max_norm_series(a)
    w = float(_RECENCY_WEIGHT)
    combined = w * r_n + (1.0 - w) * a_n
    order = (
        pd.DataFrame(
            {
                "score": combined,
                "alltime": alltime_sum,
                "name": [str(c) for c in pivot.columns],
            },
            index=pivot.columns,
        )
        .sort_values(by=["score", "alltime", "name"], ascending=[True, False, True])
        .index.tolist()
    )
    return pivot[order]


def _calculate_stats(
    pivot: pd.DataFrame,
    report_type: ReportType,
    stats_to_calculate: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    if stats_to_calculate is None:
        stats_to_calculate = list(DESIRED_STATS)
    stats_by_cat = pd.DataFrame(index=pivot.columns)
    cat_column_order: list[str] = []
    for stat_key in stats_to_calculate:
        if stat_key not in STAT_DEFINITIONS:
            continue
        stat_info = STAT_DEFINITIONS[stat_key]
        col_name = stat_info.get("name_by_cat", stat_info.get("name"))
        stats_by_cat[col_name] = stat_info["func"](pivot, 0, report_type)
        cat_column_order.append(str(col_name))
    stats_by_month = pd.DataFrame(index=pivot.index)
    month_column_order: list[str] = []
    for stat_key in stats_to_calculate:
        if stat_key not in STAT_DEFINITIONS:
            continue
        stat_info = STAT_DEFINITIONS[stat_key]
        col_name = stat_info.get("name_by_month", stat_info.get("name"))
        stats_by_month[col_name] = stat_info["func"](pivot, 1, report_type)
        month_column_order.append(str(col_name))
    return {
        "by_category": stats_by_cat.reindex(columns=cat_column_order).fillna(0),
        "by_month": stats_by_month.reindex(columns=month_column_order).fillna(0),
    }


def _matrix_to_json(arr: np.ndarray) -> list[list[float | None]]:
    """2D numeric matrix for JSON; non-finite → null."""
    a = np.asarray(arr, dtype=float)
    out: list[list[float | None]] = []
    for i in range(a.shape[0]):
        row: list[float | None] = []
        for j in range(a.shape[1]):
            v = float(a[i, j])
            row.append(None if not np.isfinite(v) else v)
        out.append(row)
    return out


def _stats_df_to_tabular(stats_df: pd.DataFrame, index_label: str) -> dict[str, Any]:
    """Stats pivot as columns + row records (frontend applies heat coloring)."""
    if stats_df.empty:
        return {"columns": [], "rows": []}
    cols = [index_label] + [str(c) for c in stats_df.columns]
    rows: list[dict[str, Any]] = []
    for idx, row in stats_df.iterrows():
        rec: dict[str, Any] = {index_label: str(idx)}
        for c in stats_df.columns:
            v = row[c]
            rec[str(c)] = None if pd.isna(v) else float(v)
        rows.append(rec)
    return {"columns": cols, "rows": rows}


@dataclass
class HeatmapBundle:
    df: pd.DataFrame
    expenses_pivot: pd.DataFrame
    income_pivot: pd.DataFrame
    net_pivot: pd.DataFrame
    expenses_pivot_log: pd.DataFrame
    income_pivot_log: pd.DataFrame
    net_pivot_normalized: pd.DataFrame
    expense_summary: dict[str, pd.DataFrame]
    income_summary: dict[str, pd.DataFrame]
    net_summary: dict[str, pd.DataFrame]
    source_path: str


def _build_bundle_from_dataframe(df: pd.DataFrame, source_label: str) -> HeatmapBundle:
    df = df.copy()
    df["תאריך"] = _heatmap_parse_dates(df["תאריך"])
    df["effective_tx_date"] = _heatmap_effective_tx_date(df)
    df["YearMonth"] = _heatmap_effective_year_month(df)

    expenses_df = df[df["בחובה"] > 0]
    expenses_pivot = (
        pd.pivot_table(
            expenses_df,
            values="בחובה",
            index="YearMonth",
            columns="קטגוריה",
            aggfunc="sum",
        )
        .fillna(0)
        .sort_index(ascending=False)
    )
    income_df = df[df["בזכות"] > 0]
    income_pivot = (
        pd.pivot_table(
            income_df,
            values="בזכות",
            index="YearMonth",
            columns="קטגוריה",
            aggfunc="sum",
        )
        .fillna(0)
        .sort_index(ascending=False)
    )
    all_cols = expenses_pivot.columns.union(income_pivot.columns)
    all_idx = expenses_pivot.index.union(income_pivot.index)
    income_aligned = income_pivot.reindex(index=all_idx, columns=all_cols).fillna(0)
    expenses_aligned = expenses_pivot.reindex(index=all_idx, columns=all_cols).fillna(0)
    net_pivot = (income_aligned - expenses_aligned).sort_index(ascending=False)

    expenses_pivot = _reorder_pivot_columns_recency_weighted(expenses_pivot)
    income_pivot = _reorder_pivot_columns_recency_weighted(income_pivot)
    net_pivot = _reorder_pivot_columns_recency_weighted(net_pivot)

    expenses_pivot_log = np.log1p(expenses_pivot)
    income_pivot_log = np.log1p(income_pivot)
    net_symlog = np.sign(net_pivot) * np.log1p(np.abs(net_pivot))

    def normalize_col(col: pd.Series) -> pd.Series:
        max_abs = col.abs().max()
        return col / max_abs if max_abs != 0 else col

    net_pivot_normalized = net_symlog.apply(normalize_col).fillna(0)

    expense_summary = _calculate_stats(expenses_pivot, "expense", DESIRED_STATS)
    income_summary = _calculate_stats(income_pivot, "income", DESIRED_STATS)
    net_summary = _calculate_stats(net_pivot, "net", DESIRED_STATS)

    return HeatmapBundle(
        df=df,
        expenses_pivot=expenses_pivot,
        income_pivot=income_pivot,
        net_pivot=net_pivot,
        expenses_pivot_log=expenses_pivot_log,
        income_pivot_log=income_pivot_log,
        net_pivot_normalized=net_pivot_normalized,
        expense_summary=expense_summary,
        income_summary=income_summary,
        net_summary=net_summary,
        source_path=source_label,
    )


def _ledger_heatmap_status() -> dict[str, Any]:
    """JSON-friendly status for the heatmap API (SQLite canonical; no Sheets pull)."""
    p = config.ledger_db_file
    exists = os.path.isfile(p)
    payload: dict[str, Any] = {
        "ledger_path": p,
        "ledger_exists": exists,
        "transaction_count": 0,
        "transaction_count_total_stored": 0,
        "transaction_count_excluded": 0,
    }
    if not exists:
        return payload

    try:
        from ledger.store import heatmap_ledger_row_counts

        counts = heatmap_ledger_row_counts(p)
        payload["transaction_count_total_stored"] = counts["transaction_count_total_stored"]
        payload["transaction_count_excluded"] = counts["transaction_count_excluded"]
        payload["transaction_count"] = counts["transaction_count"]
    except Exception:  # noqa: BLE001
        payload["transaction_count"] = -1

    return payload


def get_bundle() -> HeatmapBundle | None:
    """Load pivot data from the SQLite ledger (canonical).

    Phase: pandas pivots and normalization only — no SQL-side matrix yet. Uses
    :func:`load_transactions_dataframe_from_ledger` (migrate + full read). Control server
    startup also runs :func:`ledger.migrate_ledger_db` so DDL stays off hot paths
    where possible. A later phase could swap to SQL/materialized slices and ``read_*`` only.
    """
    db = config.ledger_db_file
    if not os.path.isfile(db):
        log.warning("heatmap: ledger database missing at %s", db)
        return None
    try:
        st = os.stat(db)
    except OSError:
        return None
    c = _bundle_cache
    if c["path"] == db and c["mtime"] == st.st_mtime and c["bundle"] is not None:
        return c["bundle"]
    from ledger import load_transactions_dataframe_from_ledger

    try:
        df = load_transactions_dataframe_from_ledger(db)
    except Exception:  # noqa: BLE001
        log.exception("heatmap: failed to load ledger %s", db)
        return None
    if df.empty:
        log.warning("heatmap: ledger empty")
        return None
    for col in ("תאריך", "בחובה", "בזכות", "קטגוריה"):
        if col not in df.columns:
            log.warning("heatmap: ledger missing column %r", col)
            return None
    df["בחובה"] = pd.to_numeric(df["בחובה"], errors="coerce").fillna(0.0)
    df["בזכות"] = pd.to_numeric(df["בזכות"], errors="coerce").fillna(0.0)
    try:
        bundle = _build_bundle_from_dataframe(df, db)
    except Exception:  # noqa: BLE001
        log.exception("heatmap: failed to build bundle from ledger")
        return None
    c["path"] = db
    c["mtime"] = st.st_mtime
    c["bundle"] = bundle
    return bundle


def _view_payload(
    _bundle: HeatmapBundle,
    pivot: pd.DataFrame,
    z_paint: pd.DataFrame,
    cmap: str,
    zcenter: float | None,
    report_type: ReportType,
    title: str,
) -> dict[str, Any]:
    """Serialize one heatmap view: raw matrices + color scale metadata for the SPA."""
    months = [str(x) for x in pivot.index.tolist()]
    categories = [str(x) for x in pivot.columns.tolist()]
    z = z_paint.values
    display = pivot.values.astype(float)

    # Monthly aggregate series for YTD and rolling-12 metrics.
    row_totals_numeric = pivot.sum(axis=1).astype(float)
    month_idx = pd.to_datetime(row_totals_numeric.index, format="%Y-%m", errors="coerce")
    monthly_df = pd.DataFrame(
        {"value": row_totals_numeric.values},
        index=pd.Index(month_idx, name="month"),
    ).dropna()
    monthly_df = monthly_df.sort_index(ascending=True)
    ytd_sum_numeric = monthly_df["value"].groupby(monthly_df.index.year).cumsum()
    ytd_avg_numeric = ytd_sum_numeric / monthly_df.index.month
    l12_sum_numeric = monthly_df["value"].rolling(window=12, min_periods=1).sum()
    l12_avg_numeric = l12_sum_numeric / monthly_df["value"].rolling(window=12, min_periods=1).count()
    ym_lookup = monthly_df.index.strftime("%Y-%m")
    ytd_sum_map = pd.Series(ytd_sum_numeric.values, index=ym_lookup)
    ytd_avg_map = pd.Series(ytd_avg_numeric.values, index=ym_lookup)
    l12_sum_map = pd.Series(l12_sum_numeric.values, index=ym_lookup)
    l12_avg_map = pd.Series(l12_avg_numeric.values, index=ym_lookup)

    return {
        "title": title,
        "reportType": report_type,
        "months": months,
        "categories": categories,
        "values": _matrix_to_json(display),
        "zPaint": _matrix_to_json(z),
        "colorScale": {
            "scheme": cmap,
            "center": None if zcenter is None else float(zcenter),
        },
        "columnTotals": [float(pivot[c].sum()) for c in pivot.columns],
        "columnAverages": [float(category_mean_recent_active(pivot[c], report_type)) for c in pivot.columns],
        "rowTotals": [float(pivot.loc[mi].sum()) for mi in pivot.index],
        "rowAverages": [float(pivot.loc[mi].mean()) for mi in pivot.index],
        "rowYtdSums": [float(ytd_sum_map.get(mi, 0.0)) for mi in pivot.index],
        "rowYtdAverages": [float(ytd_avg_map.get(mi, 0.0)) for mi in pivot.index],
        "rowRolling12Sums": [float(l12_sum_map.get(mi, 0.0)) for mi in pivot.index],
        "rowRolling12Averages": [float(l12_avg_map.get(mi, 0.0)) for mi in pivot.index],
    }


def api_snapshot() -> dict[str, Any]:
    bundle = get_bundle()
    source_status = _ledger_heatmap_status()
    if bundle is None:
        if not source_status["ledger_exists"]:
            msg = f"Could not load heatmap: ledger database not found at {config.ledger_db_file}."
        elif source_status.get("transaction_count") == 0:
            msg = "Could not load heatmap: ledger has no transactions yet."
        else:
            msg = "Could not load heatmap from ledger (missing columns or parse error — see server log)."
        return {
            "ok": False,
            "error": "missing_or_invalid_data",
            "message": msg,
            "sourceStatus": source_status,
            "views": {},
            "statsTables": {},
        }
    views = {
        "expense": _view_payload(
            bundle,
            bundle.expenses_pivot,
            bundle.expenses_pivot_log,
            "Reds",
            None,
            "expense",
            "הוצאות חודשיות לפי קטגוריה",
        ),
        "income": _view_payload(
            bundle,
            bundle.income_pivot,
            bundle.income_pivot_log,
            "Greens",
            None,
            "income",
            "הכנסות חודשיות לפי קטגוריה",
        ),
        "net": _view_payload(
            bundle,
            bundle.net_pivot,
            bundle.net_pivot_normalized,
            "RdBu",
            0.0,
            "net",
            "הכנסות נטו (הכנסות פחות הוצאות) לפי קטגוריה",
        ),
    }
    stats_tables = {
        "expense": {
            "byCategory": _stats_df_to_tabular(bundle.expense_summary["by_category"], "קטגוריה"),
            "byMonth": _stats_df_to_tabular(bundle.expense_summary["by_month"], "חודש"),
        },
        "income": {
            "byCategory": _stats_df_to_tabular(bundle.income_summary["by_category"], "קטגוריה"),
            "byMonth": _stats_df_to_tabular(bundle.income_summary["by_month"], "חודש"),
        },
        "net": {
            "byCategory": _stats_df_to_tabular(bundle.net_summary["by_category"], "קטגוריה"),
            "byMonth": _stats_df_to_tabular(bundle.net_summary["by_month"], "חודש"),
        },
    }
    return {
        "ok": True,
        "error": None,
        "message": None,
        "source": bundle.source_path,
        "sourceStatus": source_status,
        "views": views,
        "statsTables": stats_tables,
    }


def _filter_recent_months_heatmap(df: pd.DataFrame, months: int) -> pd.DataFrame:
    """Last ``months`` distinct YearMonth buckets (matching dashboard semantics)."""
    if df.empty or months <= 0:
        return df
    valid = df.dropna(subset=["YearMonth"])
    if valid.empty:
        return valid.iloc[0:0]
    months_sorted = sorted(valid["YearMonth"].unique(), reverse=True)
    keep = set(months_sorted[:months])
    return valid[valid["YearMonth"].isin(keep)]


_PERIOD_TO_MONTHS_HM = {"30d": 1, "ytd": -1, "12m": 12, "3m": 3, "6m": 6}


def _period_filter_transactions(
    df: pd.DataFrame,
    period: str,
    *,
    start_ym: str | None = None,
    end_ym: str | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df
    from ledger import dashboard_sql as dashboard_tx_sql

    bounds = dashboard_tx_sql.normalize_ym_range(start_ym, end_ym)
    if bounds is not None:
        lo, hi = bounds
        ym = df["YearMonth"].fillna("").astype(str)
        return df.loc[(ym >= lo) & (ym <= hi) & (ym != "")]

    raw = (period or "12m").strip()
    p = raw.lower()
    if p == "all":
        return df
    if len(raw) == 4 and raw.isdigit():
        pref = f"{raw}-"
        ym = df["YearMonth"].fillna("").astype(str)
        return df.loc[ym.str.startswith(pref)]
    if p == "30d":
        et = df["effective_tx_date"]
        max_d = pd.Timestamp(et.max()) if et.notna().any() else pd.NaT
        if pd.isna(max_d):
            return df.iloc[0:0]
        cutoff = max_d - pd.Timedelta(days=30)
        return df.loc[et > cutoff]
    if p == "ytd":
        et = df["effective_tx_date"]
        max_d = pd.Timestamp(et.max()) if et.notna().any() else pd.NaT
        if pd.isna(max_d):
            return df.iloc[0:0]
        year = int(max_d.year)
        ym_prefix = f"{year}-"
        return df[df["YearMonth"].fillna("").astype(str).str.startswith(ym_prefix)]
    n = _PERIOD_TO_MONTHS_HM.get(p, 12)
    if n is None or n <= 0:
        n = 12
    return _filter_recent_months_heatmap(df, n)


def _df_to_json_table(df: pd.DataFrame) -> tuple[list[str], list[dict[str, Any]]]:
    columns = [str(c) for c in df.columns]
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rec: dict[str, Any] = {}
        for c in df.columns:
            v = row[c]
            if pd.isna(v):
                rec[str(c)] = None
            elif isinstance(v, pd.Timestamp):
                rec[str(c)] = v.strftime("%Y-%m-%d")
            elif isinstance(v, (datetime, date)):
                rec[str(c)] = v.isoformat()[:10]
            elif isinstance(v, (np.integer, np.floating)):
                rec[str(c)] = float(v) if isinstance(v, np.floating) else int(v)
            elif isinstance(v, (int, float)):
                rec[str(c)] = v
            elif isinstance(v, (np.bool_, bool)):
                rec[str(c)] = bool(v)
            else:
                rec[str(c)] = str(v)
        rows.append(rec)
    return columns, rows


def _df_to_json_detail_table(frame: pd.DataFrame) -> tuple[list[str], list[dict[str, Any]]]:
    """Serialize a detail slice: each row dict includes ``id`` but ``columns`` omits it."""
    if frame.empty:
        return [], []
    if "id" not in frame.columns:
        return _df_to_json_table(frame)
    ids = frame["id"].tolist()
    display = frame.drop(columns=["id"])
    cols, rows = _df_to_json_table(display)
    for i, rid in enumerate(ids):
        if i >= len(rows):
            break
        if rid is None or (isinstance(rid, float) and np.isnan(rid)):
            rows[i]["id"] = None
        elif pd.isna(rid):
            rows[i]["id"] = None
        else:
            rows[i]["id"] = int(rid)
    return cols, rows


def _qs_first(qs: dict[str, list[str]], key: str, default: str = "") -> str:
    val = qs.get(key)
    if not val or val[0] is None:
        return default
    return str(val[0])


def _qs_int(qs: dict[str, list[str]], key: str, default: int) -> int:
    raw = _qs_first(qs, key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_DETAIL_EXTRA_COLS = [
    "notes",
    "4 ספרות",
    "statement_month",
    "ingested_at",
    "category_updated_at",
    "data_updated_at",
]
_COLS_EXP = ["תאריך", "מקור עסקה", "קטגוריה", "בחובה", "תאור מורחב", "פירוט נוסף"] + _DETAIL_EXTRA_COLS
_COLS_IN = ["תאריך", "מקור עסקה", "קטגוריה", "בזכות", "תאור מורחב", "פירוט נוסף"] + _DETAIL_EXTRA_COLS
_DETAIL_SOURCE_VISIBLE = (
    ["תאריך", "מקור עסקה", "קטגוריה", "בחובה", "בזכות", "תאור מורחב", "פירוט נוסף"]
    + _DETAIL_EXTRA_COLS
)


def _pick_cols(df: pd.DataFrame, cols: list[str]) -> list[str]:
    return [c for c in cols if c in df.columns]


def _detail_column_order(df: pd.DataFrame, visible: list[str]) -> list[str]:
    """Include SQLite ``id`` first when present (internal key; omitted from JSON column headers)."""
    return _pick_cols(df, ["id"] + visible)


def _sort_detail_frame(frame: pd.DataFrame, source_df: pd.DataFrame | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    eff: pd.Series | None = None
    if source_df is not None and "effective_tx_date" in source_df.columns:
        eff = source_df["effective_tx_date"].reindex(frame.index)
    elif "effective_tx_date" in frame.columns:
        eff = frame["effective_tx_date"]
    if eff is not None and bool(eff.notna().any()):
        return (
            frame.assign(__eff=eff)
            .sort_values("__eff", ascending=False, na_position="last")
            .drop(columns=["__eff"])
        )
    if "תאריך" in frame.columns:
        return frame.sort_values("תאריך", ascending=False)
    return frame


def _detail_frames_cell(
    bundle: HeatmapBundle, report_type: ReportType, year_month: str, category: str
) -> tuple[str, list[tuple[str | None, pd.DataFrame]]] | None:
    df = bundle.df
    cols_show_exp = _detail_column_order(df, _COLS_EXP)
    cols_show_in = _detail_column_order(df, _COLS_IN)
    if report_type == "expense":
        pivot = bundle.expenses_pivot
        if year_month not in pivot.index or category not in pivot.columns:
            return None
        if float(pivot.loc[year_month, category]) <= 0:
            return None
        mask = (df["YearMonth"] == year_month) & (df["קטגוריה"] == category) & (df["בחובה"] > 0)
        details = _sort_detail_frame(df.loc[mask, cols_show_exp], df)
        title = f"פירוט הוצאות עבור {category} ב-{year_month}"
        return (title, [(None, details)])
    if report_type == "income":
        pivot = bundle.income_pivot
        if year_month not in pivot.index or category not in pivot.columns:
            return None
        if float(pivot.loc[year_month, category]) <= 0:
            return None
        mask = (df["YearMonth"] == year_month) & (df["קטגוריה"] == category) & (df["בזכות"] > 0)
        details = _sort_detail_frame(df.loc[mask, cols_show_in], df)
        title = f"פירוט הכנסות עבור {category} ב-{year_month}"
        return (title, [(None, details)])
    pivot = bundle.net_pivot
    if year_month not in pivot.index or category not in pivot.columns:
        return None
    if float(pivot.loc[year_month, category]) == 0:
        return None
    income_mask = (df["YearMonth"] == year_month) & (df["קטגוריה"] == category) & (df["בזכות"] > 0)
    expense_mask = (df["YearMonth"] == year_month) & (df["קטגוריה"] == category) & (df["בחובה"] > 0)
    income_df = _sort_detail_frame(df.loc[income_mask, cols_show_in], df)
    expense_df = _sort_detail_frame(df.loc[expense_mask, cols_show_exp], df)
    title = f"פירוט תנועות עבור {category} ב-{year_month}"
    return (title, [("הכנסות", income_df), ("הוצאות", expense_df)])


def _detail_frames_month(
    bundle: HeatmapBundle, report_type: ReportType, year_month: str
) -> tuple[str, list[tuple[str | None, pd.DataFrame]]] | None:
    df = bundle.df
    if not (df["YearMonth"] == year_month).any():
        return None
    cols_show_exp = _detail_column_order(df, _COLS_EXP)
    cols_show_in = _detail_column_order(df, _COLS_IN)
    if report_type == "expense":
        mask = (df["YearMonth"] == year_month) & (df["בחובה"] > 0)
        details = _sort_detail_frame(df.loc[mask, cols_show_exp], df)
        if details.empty:
            return None
        return (f"כל ההוצאות ב-{year_month}", [(None, details)])
    if report_type == "income":
        mask = (df["YearMonth"] == year_month) & (df["בזכות"] > 0)
        details = _sort_detail_frame(df.loc[mask, cols_show_in], df)
        if details.empty:
            return None
        return (f"כל ההכנסות ב-{year_month}", [(None, details)])
    income_mask = (df["YearMonth"] == year_month) & (df["בזכות"] > 0)
    expense_mask = (df["YearMonth"] == year_month) & (df["בחובה"] > 0)
    income_df = _sort_detail_frame(df.loc[income_mask, cols_show_in], df)
    expense_df = _sort_detail_frame(df.loc[expense_mask, cols_show_exp], df)
    if income_df.empty and expense_df.empty:
        return None
    return (f"כל התנועות ב-{year_month}", [("הכנסות", income_df), ("הוצאות", expense_df)])


def _detail_frames_category(
    bundle: HeatmapBundle,
    report_type: ReportType,
    category: str,
    period: str,
    *,
    start_ym: str | None = None,
    end_ym: str | None = None,
) -> tuple[str, list[tuple[str | None, pd.DataFrame]]] | None:
    df = bundle.df
    sub = _period_filter_transactions(df, period, start_ym=start_ym, end_ym=end_ym)
    if sub.empty:
        return None
    work = sub.copy()
    work["__cat__"] = work["קטגוריה"].fillna("").astype(str).str.strip()
    work.loc[work["__cat__"] == "", "__cat__"] = "(uncategorized)"
    cols_show_exp = _detail_column_order(work, _COLS_EXP)
    cols_show_in = _detail_column_order(work, _COLS_IN)

    from ledger import dashboard_sql as dashboard_tx_sql

    bounds_lbl = dashboard_tx_sql.normalize_ym_range(start_ym, end_ym)
    if bounds_lbl is not None:
        lo, hi = bounds_lbl
        win_lbl = f"{lo} – {hi}"
    elif (period or "").strip().lower() == "all":
        win_lbl = "all"
    else:
        win_lbl = period

    if report_type == "expense":
        mask = (work["__cat__"] == category) & (work["בחובה"] > 0)
        details = _sort_detail_frame(work.loc[mask, cols_show_exp], work)
        if details.empty:
            return None
        return (f"הוצאות — {category} ({win_lbl})", [(None, details)])
    if report_type == "income":
        mask = (work["__cat__"] == category) & (work["בזכות"] > 0)
        details = _sort_detail_frame(work.loc[mask, cols_show_in], work)
        if details.empty:
            return None
        return (f"הכנסות — {category} ({win_lbl})", [(None, details)])
    income_mask = (work["__cat__"] == category) & (work["בזכות"] > 0)
    expense_mask = (work["__cat__"] == category) & (work["בחובה"] > 0)
    income_df = _sort_detail_frame(work.loc[income_mask, cols_show_in], work)
    expense_df = _sort_detail_frame(work.loc[expense_mask, cols_show_exp], work)
    if income_df.empty and expense_df.empty:
        return None
    return (f"נטו — {category} ({win_lbl})", [("הכנסות", income_df), ("הוצאות", expense_df)])


def _detail_frames_source(
    bundle: HeatmapBundle, source_key: str, months: int
) -> tuple[str, list[tuple[str | None, pd.DataFrame]]] | None:
    df = bundle.df
    if "מקור עסקה" not in df.columns:
        return None
    sub = _filter_recent_months_heatmap(df, months)
    if sub.empty:
        return None
    work = sub.copy()
    work["__src__"] = work["מקור עסקה"].fillna("").astype(str).str.strip()
    work.loc[work["__src__"] == "", "__src__"] = "(unknown)"
    sk = source_key.strip()
    mask = work["__src__"] == sk
    if not mask.any():
        return None
    cols = _detail_column_order(work, _DETAIL_SOURCE_VISIBLE)
    details = _sort_detail_frame(work.loc[mask, cols], work)
    title = f"תנועות — מקור «{sk}» ({months} חודשים)"
    return (title, [(None, details)])


def _detail_frames_source_category(
    bundle: HeatmapBundle,
    report_type: ReportType,
    source_key: str,
    category: str,
    months: int,
) -> tuple[str, list[tuple[str | None, pd.DataFrame]]] | None:
    df = bundle.df
    if "מקור עסקה" not in df.columns:
        return None
    sub = _filter_recent_months_heatmap(df, months)
    if sub.empty:
        return None
    work = sub.copy()
    work["__src__"] = work["מקור עסקה"].fillna("").astype(str).str.strip()
    work.loc[work["__src__"] == "", "__src__"] = "(unknown)"
    work["__cat__"] = work["קטגוריה"].fillna("").astype(str).str.strip()
    work.loc[work["__cat__"] == "", "__cat__"] = "(uncategorized)"
    sk = source_key.strip()
    cat = category.strip()
    cols_show_exp = _detail_column_order(work, _COLS_EXP)
    cols_show_in = _detail_column_order(work, _COLS_IN)
    if report_type == "expense":
        mask = (work["__src__"] == sk) & (work["__cat__"] == cat) & (work["בחובה"] > 0)
        details = _sort_detail_frame(work.loc[mask, cols_show_exp], work)
        if details.empty:
            return None
        return (f"הוצאות — מקור «{sk}» · {cat}", [(None, details)])
    if report_type == "income":
        mask = (work["__src__"] == sk) & (work["__cat__"] == cat) & (work["בזכות"] > 0)
        details = _sort_detail_frame(work.loc[mask, cols_show_in], work)
        if details.empty:
            return None
        return (f"הכנסות — מקור «{sk}» · {cat}", [(None, details)])
    income_mask = (work["__src__"] == sk) & (work["__cat__"] == cat) & (work["בזכות"] > 0)
    expense_mask = (work["__src__"] == sk) & (work["__cat__"] == cat) & (work["בחובה"] > 0)
    income_df = _sort_detail_frame(work.loc[income_mask, cols_show_in], work)
    expense_df = _sort_detail_frame(work.loc[expense_mask, cols_show_exp], work)
    if income_df.empty and expense_df.empty:
        return None
    return (f"נטו — מקור «{sk}» · {cat}", [("הכנסות", income_df), ("הוצאות", expense_df)])


def build_detail_frames_from_qs(
    bundle: HeatmapBundle, qs: dict[str, list[str]]
) -> tuple[str, list[tuple[str | None, pd.DataFrame]]] | None:
    """Resolve drill-down: ``src``+``cat`` (source×category), ``src`` alone, ``ym``+``cat`` (cell), etc."""
    src = _qs_first(qs, "src", "").strip() or _qs_first(qs, "source", "").strip()
    ym = _qs_first(qs, "ym", "").strip()
    cat = _qs_first(qs, "cat", "").strip()
    rt_raw = _qs_first(qs, "type", "expense").strip().lower()
    rt: ReportType = "expense"
    if rt_raw in ("expense", "income", "net"):
        rt = rt_raw  # type: ignore[assignment]
    period = _qs_first(qs, "period", "12m").strip().lower()
    start_ym_raw = _qs_first(qs, "start_ym", "").strip()
    end_ym_raw = _qs_first(qs, "end_ym", "").strip()
    start_ym = start_ym_raw or None
    end_ym = end_ym_raw or None
    months = max(1, _qs_int(qs, "months", 12))

    if src and cat:
        return _detail_frames_source_category(bundle, rt, src, cat, months)
    if src:
        return _detail_frames_source(bundle, src, months)
    if ym and cat:
        return _detail_frames_cell(bundle, rt, ym, cat)
    if ym and not cat:
        return _detail_frames_month(bundle, rt, ym)
    if cat and not ym:
        return _detail_frames_category(
            bundle, rt, cat, period, start_ym=start_ym, end_ym=end_ym
        )
    return None


def detail_api_payload(qs: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    """JSON body and suggested HTTP status for ``GET /heatmap/api/detail``."""
    bundle = get_bundle()
    if bundle is None:
        return 503, {
            "ok": False,
            "error": "unavailable",
            "message": "Ledger data not available.",
        }
    built = build_detail_frames_from_qs(bundle, qs)
    if built is None:
        return 404, {"ok": False, "error": "not_found", "message": "No matching rows."}
    title, frames = built
    sections: list[dict[str, Any]] = []
    for sub, frame in frames:
        cols, rows = _df_to_json_detail_table(frame)
        sections.append({"subtitle": sub, "columns": cols, "rows": rows})
    return 200, {"ok": True, "title": title, "sections": sections}


def _ledger_patch_http_status(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 200
    err = result.get("error")
    if err == "not_found":
        return 404
    if err == "fingerprint_conflict":
        return 409
    return 400


def patch_ledger_transaction(raw_body: bytes) -> tuple[int, dict[str, Any]]:
    """Parse JSON body and apply :func:`ledger.patch_ledger_transaction_by_id`."""
    from ledger import patch_ledger_transaction_by_id

    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return 400, {"ok": False, "error": "invalid_json", "message": "Invalid JSON body."}

    row_id = data.get("id")
    patch = data.get("patch")
    if row_id is None or not isinstance(patch, dict):
        return 400, {
            "ok": False,
            "error": "validation_error",
            "message": "Body must include integer id and patch object.",
        }

    try:
        rid = int(row_id)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "validation_error", "message": "id must be an integer."}

    confirm_change = bool(data.get("confirm_fingerprint_change"))
    phrase = str(data.get("confirm_fingerprint_phrase") or "")

    result = patch_ledger_transaction_by_id(
        config.ledger_db_file,
        rid,
        patch,
        confirm_fingerprint_change=confirm_change,
        confirm_fingerprint_phrase=phrase,
    )
    if result.get("ok"):
        invalidate_bundle_cache()
    return _ledger_patch_http_status(result), result
