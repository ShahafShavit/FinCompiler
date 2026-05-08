"""
Interactive heatmap data and HTML for /heatmap (native HTML/CSS/JS, no Plotly).

Logic mirrors ``reporting.interactive_look.InteractiveReportGenerator``: same CSV columns,
pivots, log/symlog normalization, summary stats styling, and per-cell drill-down.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import parse_qs

import matplotlib as mpl
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

import config

from . import control_nav

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


def _style_stats_table(stats_df: pd.DataFrame, report_type: ReportType) -> str:
    if stats_df.empty:
        return "<p class='no-data'>No statistics to display.</p>"
    if report_type == "net":
        cm_seq = "RdBu"
    elif report_type == "income":
        cm_seq = "Greens"
    else:
        cm_seq = "Reds"
    cm_variance = "Oranges"
    cm_count = "Purples"

    if report_type == "net":

        def transform(x: Any) -> Any:
            v = float(x)
            return np.sign(v) * np.log1p(abs(v))

    else:
        transform = np.log1p

    def _text_color_for_bg(bg_hex: str) -> str:
        """Return readable foreground color for a hex background."""
        try:
            r, g, b = mcolors.to_rgb(bg_hex)
        except ValueError:
            return "#f4f6fb"
        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return "#111318" if luminance > 0.58 else "#f4f6fb"

    def _apply_color_to_series(series: pd.Series, cmap_name: str, transform_func: Any) -> list[str]:
        series_transformed = series.astype(float).map(transform_func)
        min_val, max_val = series_transformed.min(), series_transformed.max()
        if min_val == max_val:
            return [""] * len(series)
        norm = mcolors.Normalize(vmin=min_val, vmax=max_val)
        cmap = mpl.colormaps.get_cmap(cmap_name)
        colors = series_transformed.map(
            lambda x: mcolors.to_hex(cmap(norm(x))) if pd.notna(x) else ""
        )
        return [
            (f"background-color: {c}; color: {_text_color_for_bg(c)}; text-shadow: none;" if c else "")
            for c in colors
        ]

    seq_cols = [
        "סך הכל (Total)",
        "ממוצע חודשי (Avg)",
        "ממוצע לקטגוריה (Avg)",
        "חציון (Median)",
        "מקסימום (Max)",
        "מינימום (Min)",
        "אחוזון 75 (75th Pctl)",
        "אחוזון 25 (25th Pctl)",
    ]
    valid_seq_cols = [c for c in seq_cols if c in stats_df.columns]
    styling_dict = {col: "{:,.2f}₪" for col in valid_seq_cols}
    styler = stats_df.style
    if valid_seq_cols:
        styler = styler.apply(
            _apply_color_to_series,
            cmap_name=cm_seq,
            transform_func=transform,
            subset=valid_seq_cols,
            axis=0,
        )
    if "סטיית תקן (Std Dev)" in stats_df.columns:
        styler = styler.apply(
            _apply_color_to_series,
            cmap_name=cm_variance,
            transform_func=transform,
            subset=["סטיית תקן (Std Dev)"],
            axis=0,
        )
    if "ספירה (Count > 0)" in stats_df.columns:
        styler = styler.apply(
            _apply_color_to_series,
            cmap_name=cm_count,
            transform_func=transform,
            subset=["ספירה (Count > 0)"],
            axis=0,
        )
    styler = styler.format(styling_dict)
    styler = styler.set_table_attributes('class="styled-table"')
    return styler.to_html()


def _heatmap_cell_colors(z_paint: np.ndarray, cmap_name: str, center: float | None) -> list[list[str]]:
    """Return hex background colors for each cell; z_paint same shape as pivot (rows=months, cols=cats)."""
    flat = z_paint.astype(float).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return [["#333337" for _ in range(z_paint.shape[1])] for _ in range(z_paint.shape[0])]
    vmin, vmax = float(np.nanmin(flat)), float(np.nanmax(flat))
    cmap = mpl.colormaps.get_cmap(cmap_name)
    if center is not None and vmin < center < vmax:
        norm: mcolors.Normalize = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=center, vmax=vmax)
    else:
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax if vmax != vmin else vmin + 1.0)
    out: list[list[str]] = []
    for i in range(z_paint.shape[0]):
        row: list[str] = []
        for j in range(z_paint.shape[1]):
            v = z_paint[i, j]
            if not np.isfinite(v):
                row.append("#333337")
            else:
                row.append(mcolors.to_hex(cmap(norm(v))))
        out.append(row)
    return out


def _format_cell_money(v: float) -> str:
    sign = "-" if v < 0 else ""
    a = abs(v)
    if abs(a - round(a)) < 0.01:
        return f"{sign}{a:,.0f}₪"
    return f"{sign}{a:,.2f}₪"


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
        import sqlite3

        from pipeline.ledger import LEDGER_SQL_TX_INCLUDED, migrate_ledger_db

        migrate_ledger_db(p)
        conn = sqlite3.connect(p)
        try:
            total = int(conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0] or 0)
            inc = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM ledger_transaction WHERE {LEDGER_SQL_TX_INCLUDED}"
                ).fetchone()[0]
                or 0
            )
            payload["transaction_count_total_stored"] = total
            payload["transaction_count_excluded"] = max(0, total - inc)
            payload["transaction_count"] = inc
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        payload["transaction_count"] = -1

    return payload


def get_bundle() -> HeatmapBundle | None:
    """Load pivot data from the SQLite ledger (canonical).

    Phase: pandas pivots and normalization only — no SQL-side matrix yet. Uses
    :func:`load_transactions_dataframe_from_ledger` (migrate + full read). Control server
    startup also runs :func:`pipeline.ledger.migrate_ledger_db` so DDL stays off hot paths
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
    from pipeline.ledger import load_transactions_dataframe_from_ledger

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
    bundle: HeatmapBundle,
    pivot: pd.DataFrame,
    z_paint: pd.DataFrame,
    cmap: str,
    zcenter: float | None,
    report_type: ReportType,
    title: str,
) -> dict[str, Any]:
    months = [str(x) for x in pivot.index.tolist()]
    categories = [str(x) for x in pivot.columns.tolist()]
    z = z_paint.values
    display = pivot.values.astype(float)
    colors = _heatmap_cell_colors(z, cmap, zcenter)
    fg_colors: list[list[str]] = []
    for i in range(len(months)):
        row_fg: list[str] = []
        for j in range(len(categories)):
            bg = colors[i][j] if i < len(colors) and j < len(colors[i]) else "#333337"
            try:
                r, g, b = mcolors.to_rgb(bg)
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                row_fg.append("#111318" if lum > 0.58 else "#f4f6fb")
            except ValueError:
                row_fg.append("#f4f6fb")
        fg_colors.append(row_fg)
    clickable: list[list[bool]] = []
    for i, ym in enumerate(months):
        row_b: list[bool] = []
        for j, cat in enumerate(categories):
            v = display[i, j] if i < display.shape[0] and j < display.shape[1] else 0.0
            if report_type == "net":
                row_b.append(bool(v != 0))
            else:
                row_b.append(bool(v > 0))
        clickable.append(row_b)
    labels = [[_format_cell_money(display[i, j]) for j in range(len(categories))] for i in range(len(months))]
    column_totals = [_format_cell_money(float(pivot[c].sum())) for c in pivot.columns]
    column_averages = [
        _format_cell_money(category_mean_recent_active(pivot[c], report_type)) for c in pivot.columns
    ]
    row_totals = [_format_cell_money(float(pivot.loc[mi].sum())) for mi in pivot.index]
    row_averages = [_format_cell_money(float(pivot.loc[mi].mean())) for mi in pivot.index]

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

    ytd_sums = [_format_cell_money(float(ytd_sum_map.get(mi, 0.0))) for mi in pivot.index]
    ytd_averages = [_format_cell_money(float(ytd_avg_map.get(mi, 0.0))) for mi in pivot.index]
    rolling12_sums = [_format_cell_money(float(l12_sum_map.get(mi, 0.0))) for mi in pivot.index]
    rolling12_averages = [_format_cell_money(float(l12_avg_map.get(mi, 0.0))) for mi in pivot.index]
    return {
        "title": title,
        "reportType": report_type,
        "months": months,
        "categories": categories,
        "labels": labels,
        "cellBg": colors,
        "cellFg": fg_colors,
        "clickable": clickable,
        "columnTotals": column_totals,
        "columnAverages": column_averages,
        "rowTotals": row_totals,
        "rowAverages": row_averages,
        "rowYtdSums": ytd_sums,
        "rowYtdAverages": ytd_averages,
        "rowRolling12Sums": rolling12_sums,
        "rowRolling12Averages": rolling12_averages,
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
            "statsHtml": {},
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
    stats_html = {
        "expense": {
            "byCategory": _style_stats_table(bundle.expense_summary["by_category"], "expense"),
            "byMonth": _style_stats_table(bundle.expense_summary["by_month"], "expense"),
        },
        "income": {
            "byCategory": _style_stats_table(bundle.income_summary["by_category"], "income"),
            "byMonth": _style_stats_table(bundle.income_summary["by_month"], "income"),
        },
        "net": {
            "byCategory": _style_stats_table(bundle.net_summary["by_category"], "net"),
            "byMonth": _style_stats_table(bundle.net_summary["by_month"], "net"),
        },
    }
    return {
        "ok": True,
        "error": None,
        "message": None,
        "source": bundle.source_path,
        "sourceStatus": source_status,
        "views": views,
        "statsHtml": stats_html,
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


def _period_filter_transactions(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty:
        return df
    p = (period or "12m").lower().strip()
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
_COLS_EXP = ["תאריך", "מקור עסקה", "בחובה", "תאור מורחב", "פירוט נוסף"] + _DETAIL_EXTRA_COLS
_COLS_IN = ["תאריך", "מקור עסקה", "בזכות", "תאור מורחב", "פירוט נוסף"] + _DETAIL_EXTRA_COLS
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
    bundle: HeatmapBundle, report_type: ReportType, category: str, period: str
) -> tuple[str, list[tuple[str | None, pd.DataFrame]]] | None:
    df = bundle.df
    sub = _period_filter_transactions(df, period)
    if sub.empty:
        return None
    work = sub.copy()
    work["__cat__"] = work["קטגוריה"].fillna("").astype(str).str.strip()
    work.loc[work["__cat__"] == "", "__cat__"] = "(uncategorized)"
    cols_show_exp = _detail_column_order(work, _COLS_EXP + ["קטגוריה"])
    cols_show_in = _detail_column_order(work, _COLS_IN + ["קטגוריה"])
    if report_type == "expense":
        mask = (work["__cat__"] == category) & (work["בחובה"] > 0)
        details = _sort_detail_frame(work.loc[mask, cols_show_exp], work)
        if details.empty:
            return None
        return (f"הוצאות — {category} ({period})", [(None, details)])
    if report_type == "income":
        mask = (work["__cat__"] == category) & (work["בזכות"] > 0)
        details = _sort_detail_frame(work.loc[mask, cols_show_in], work)
        if details.empty:
            return None
        return (f"הכנסות — {category} ({period})", [(None, details)])
    income_mask = (work["__cat__"] == category) & (work["בזכות"] > 0)
    expense_mask = (work["__cat__"] == category) & (work["בחובה"] > 0)
    income_df = _sort_detail_frame(work.loc[income_mask, cols_show_in], work)
    expense_df = _sort_detail_frame(work.loc[expense_mask, cols_show_exp], work)
    if income_df.empty and expense_df.empty:
        return None
    return (f"נטו — {category} ({period})", [("הכנסות", income_df), ("הוצאות", expense_df)])


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
        return _detail_frames_category(bundle, rt, cat, period)
    return None


def detail_page_html_from_qs(bundle: HeatmapBundle, qs: dict[str, list[str]]) -> str | None:
    """Full HTML document for legacy browser loads."""
    built = build_detail_frames_from_qs(bundle, qs)
    if built is None:
        return None
    title, frames = built
    parts = [f"<h1>{html.escape(title)}</h1>"]
    for sub, frame in frames:
        if sub:
            parts.append(f"<h2>{html.escape(sub)}</h2>")
        if frame.empty:
            parts.append("<p class='no-data'>אין נתונים</p>")
        else:
            html_frame = frame.drop(columns=["id"], errors="ignore")
            parts.append(html_frame.to_html(index=False, classes="styled-table", float_format="%.2f"))
    return _wrap_detail_document("".join(parts))


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


def ledger_transaction_patch_api(raw_body: bytes) -> tuple[int, dict[str, Any]]:
    """Parse JSON body and apply :func:`pipeline.ledger.patch_ledger_transaction_by_id`."""
    from pipeline.ledger import patch_ledger_transaction_by_id

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


def _wrap_detail_document(inner_body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="he">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>פירוט תנועות</title>
  {_heatmap_shared_css()}
</head>
<body class="detail-page">
  {control_nav.control_topnav_html()}
  {inner_body}
  <p class="hint"><a href="/heatmap/">חזרה למפת חום</a></p>
</body>
</html>
"""


def _heatmap_shared_css() -> str:
    return (
        "<style>"
        + control_nav.control_topnav_css()
        + """
      :root {
        font-family: system-ui, "Segoe UI", Roboto, sans-serif;
        background: #121316;
        color: #e8e8ec;
        line-height: 1.45;
      }
      body { margin: 0; padding: 1rem 1rem 2rem; direction: rtl; }
      body.detail-page { max-width: 56rem; margin: 0 auto; }
      h1 { font-size: 1.25rem; font-weight: 600; margin: 0 0 0.75rem 0; color: #f1f1f4; }
      h2 { font-size: 1rem; font-weight: 600; margin: 1.25rem 0 0.5rem 0; color: #c8cad4;
           border-bottom: 1px solid #2b2c33; padding-bottom: 0.35rem; }
      .hint { font-size: 0.85rem; opacity: 0.75; margin-top: 1.5rem; }
      .no-data { text-align: center; color: #888; margin: 0.75rem 0; }
      table.styled-table { border-collapse: collapse; width: 100%; margin: 0.75rem 0;
        font-size: 0.88rem; box-shadow: 0 2px 8px rgba(0,0,0,0.25); }
      .styled-table th, .styled-table td { padding: 8px 10px; text-align: right;
        border: 1px solid #2b2c33; }
      .stats-table-container .styled-table td {
        direction: ltr;
        unicode-bidi: isolate;
        text-align: right;
      }
      .styled-table thead th { background: #2d4a2f; color: #e8f5e9; }
      .stats-table-container .styled-table thead th {
        position: sticky;
        top: 0;
        z-index: 2;
      }
      .styled-table tbody tr:hover { background: #1e1f24 !important; }
      .tabs { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.5rem 0 1rem; }
      .tabs button {
        font: inherit; cursor: pointer; padding: 0.4rem 0.75rem; border-radius: 8px;
        border: 1px solid #3a3b44; background: #1c1d22; color: #c8cad4;
      }
      .tabs button.active { border-color: #4c6ef5; background: #2a2f4a; color: #e8e8ec; }
      .heatmap-title { font-size: 1.1rem; font-weight: 600; margin: 0.25rem 0 0.75rem; }
      .heatmap-wrap { overflow: auto; max-width: 100%; margin-bottom: 1.5rem;
        max-height: 78vh; border: 1px solid #2b2c33; border-radius: 8px; background: #0b0c0f; }
      table.hm-grid { border-collapse: separate; border-spacing: 1px;
        font-size: 0.74rem; margin: 0; table-layout: auto; width: max-content; }
      table.hm-grid {
        --hm-month-col-w: 7.3rem;
        --hm-metric-col-w: 4.6rem;
      }
      table.hm-grid th, table.hm-grid td {
        min-width: 4.15rem; padding: 5px 7px; text-align: center;
        vertical-align: middle; white-space: nowrap;
      }
      table.hm-grid th { background: #1c1d22; color: #aeb4c0; font-weight: 600;
        position: sticky; top: 0; z-index: 2; }
      table.hm-grid th.row-h {
        position: sticky; right: 0; z-index: 3; background: #16171c;
        min-width: var(--hm-month-col-w); text-align: right; padding-inline: 0.55rem;
      }
      table.hm-grid th.corner { z-index: 4; right: 0; top: 0; background: #14151a; }
      table.hm-grid thead th.hm-colsum {
        top: 0; z-index: 3; background: #25262e; color: #dce0ea; font-size: 0.68rem;
        font-weight: 600; border-bottom: 1px solid #3a3b44;
      }
      table.hm-grid thead th.hm-colsum .colsum-wrap {
        display: grid; grid-template-rows: auto auto; gap: 0.15rem;
      }
      table.hm-grid thead th.hm-colsum .colsum-wrap > div {
        display: flex; align-items: baseline; justify-content: space-between; gap: 0.4rem;
      }
      table.hm-grid thead th.hm-colsum .metric-label {
        color: #aeb4c0; font-size: 0.64rem; letter-spacing: 0.02em;
      }
      table.hm-grid thead th.hm-colsum .metric-val {
        color: #e6e9f0; font-size: 0.7rem; direction: ltr; unicode-bidi: isolate;
      }
      table.hm-grid thead tr:nth-child(2) th {
        top: 2.35rem; z-index: 2; background: #1c1d22;
      }
      table.hm-grid thead th.hm-metric-h {
        top: 0; z-index: 5; background: #1a1b20; color: #c4c8d4;
        min-width: var(--hm-metric-col-w); vertical-align: middle; line-height: 1.2;
      }
      table.hm-grid th.hm-rowsum-h, table.hm-grid td.hm-rowtot {
        right: var(--hm-month-col-w);
      }
      table.hm-grid th.hm-rowavg-h, table.hm-grid td.hm-rowavg {
        right: calc(var(--hm-month-col-w) + (var(--hm-metric-col-w) * 1));
      }
      table.hm-grid th.hm-ytdsum-h, table.hm-grid td.hm-ytdsum {
        right: calc(var(--hm-month-col-w) + (var(--hm-metric-col-w) * 2));
      }
      table.hm-grid th.hm-ytdavg-h, table.hm-grid td.hm-ytdavg {
        right: calc(var(--hm-month-col-w) + (var(--hm-metric-col-w) * 3));
      }
      table.hm-grid th.hm-l12sum-h, table.hm-grid td.hm-l12sum {
        right: calc(var(--hm-month-col-w) + (var(--hm-metric-col-w) * 4));
      }
      table.hm-grid th.hm-l12avg-h, table.hm-grid td.hm-l12avg {
        right: calc(var(--hm-month-col-w) + (var(--hm-metric-col-w) * 5));
      }
      table.hm-grid tbody td.hm-rowtot {
        position: sticky; z-index: 3; background: #1a1b20; color: #dce0ea;
        font-weight: 600; font-size: 0.7rem; border: 1px solid #2b2c33;
      }
      table.hm-grid tbody td.hm-rowmetric {
        position: sticky; z-index: 3; background: #191a1f; color: #dce0ea;
        font-weight: 600; font-size: 0.69rem; border: 1px solid #2b2c33;
      }
      table.hm-grid tbody tr:nth-child(even) th.row-h {
        background: #1a1b22;
      }
      table.hm-grid tbody tr:nth-child(even) td.hm-rowmetric {
        background: #1c1d24;
      }
      table.hm-grid tbody tr:nth-child(even) td.cell {
        box-shadow: inset 0 0 0 9999px rgba(255, 255, 255, 0.025);
      }
      table.hm-grid tbody tr.year-start th.row-h {
        border-top: 2px solid #5d7cff;
      }
      table.hm-grid tbody tr.group-boundary th.row-h {
        border-top: 2px dashed #4a4d57;
      }
      table.hm-grid th.row-h .l12-chip {
        font-size: 0.61rem; color: #9da3b7; background: #262a33;
        border: 1px dashed #4a4d57; border-radius: 999px; padding: 0.06rem 0.3rem;
      }
      table.hm-grid th.row-h .month-markers {
        display: inline-flex; flex-wrap: wrap; gap: 0.22rem; margin-left: 0.35rem;
      }
      table.hm-grid th.row-h .month-label {
        color: #d9deea;
      }
      table.hm-grid tbody th.row-h { z-index: 4; }
      table.hm-grid td.cell {
        cursor: default; color: #f4f6fb; font-weight: 600; text-shadow: none;
        direction: ltr; unicode-bidi: isolate;
      }
      table.hm-grid td.hm-rowtot, table.hm-grid th.hm-colsum {
        direction: ltr; unicode-bidi: isolate;
      }
      table.hm-grid td.cell.clickable { cursor: pointer; }
      table.hm-grid td.cell.clickable:hover { filter: brightness(1.12); outline: 1px solid #fff8; }
      .stats-container { display: flex; flex-wrap: wrap; gap: 1.25rem; margin-top: 0.5rem; }
      .stats-table-container { flex: 1; min-width: min(100%, 22rem); overflow-x: auto; }
      .err-banner {
        background: #3a1f1f; border: 1px solid #6b2a2a; color: #ffb4b4;
        padding: 0.75rem 1rem; border-radius: 8px; margin: 0.5rem 0 1rem;
      }
      .subtle { font-size: 0.82rem; opacity: 0.65; margin-bottom: 0.35rem; }
      .heatmap-toolbar {
        display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
        margin: 0.25rem 0 0.85rem;
      }
      .heatmap-toolbar button#btn-refresh {
        font: inherit; cursor: pointer; padding: 0.45rem 0.8rem; border-radius: 8px;
        border: 1px solid #4c6ef5; background: #4c6ef5; color: #fff;
      }
      .heatmap-toolbar button#btn-refresh:disabled {
        opacity: 0.45; cursor: not-allowed;
      }
      .heatmap-toolbar #refresh-status { font-size: 0.82rem; opacity: 0.8; max-width: 42rem; }
    </style>
"""
    )


def handle_detail_query(query: str) -> tuple[int, bytes, str]:
    """Legacy full-page HTML drill-down (see ``/heatmap/legacy-detail``)."""
    qs = parse_qs(query, keep_blank_values=True)
    bundle = get_bundle()
    if bundle is None:
        return 503, b"Data not available", "text/plain; charset=utf-8"
    page = detail_page_html_from_qs(bundle, qs)
    if page is None:
        return 404, b"Not found", "text/plain; charset=utf-8"
    return 200, page.encode("utf-8"), "text/html; charset=utf-8"
