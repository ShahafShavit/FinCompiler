"""
Interactive heatmap data and HTML for /heatmap (native HTML/CSS/JS, no Plotly).

Logic mirrors ``interactive_look_handler.InteractiveReportGenerator``: same CSV columns,
pivots, log/symlog normalization, summary stats styling, and per-cell drill-down.
"""

from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
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


def heatmap_page_script_path() -> Path:
    """On-disk path to the heatmap UI script (also served as a static asset by ``web_control.server``)."""
    return Path(__file__).resolve().parent / "heatmap_page_script.js"


# Heatmap-only: normalize mixed sheet/CSV date strings for bucketing (ISO YYYY-MM-DD first,
# then D/M/Y, then pandas fallbacks). Does not affect compile or other pipelines.
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

    ts = pd.to_datetime(s, dayfirst=False, format="mixed", errors="coerce")
    if pd.notna(ts):
        return ts.normalize()
    ts = pd.to_datetime(s, dayfirst=True, format="mixed", errors="coerce")
    return ts.normalize() if pd.notna(ts) else pd.NaT


def _heatmap_parse_dates(series: pd.Series) -> pd.Series:
    out = series.map(_heatmap_parse_one_date)
    if not isinstance(out, pd.Series):
        out = pd.Series(out, index=series.index)
    return pd.to_datetime(out, errors="coerce")


STAT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "total": {
        "name": "סך הכל (Total)",
        "func": lambda p, axis, rt: p.sum(axis=axis),
    },
    "mean": {
        "name_by_cat": "ממוצע חודשי (Avg)",
        "name_by_month": "ממוצע לקטגוריה (Avg)",
        "func": lambda p, axis, rt: p.mean(axis=axis),
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
        return [f"background-color: {c}" for c in colors]

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
    if abs(v - round(v)) < 0.01:
        return f"{v:,.0f}₪"
    return f"{v:,.2f}₪"


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


def _build_bundle(csv_path: str) -> HeatmapBundle:
    df = pd.read_csv(csv_path)
    df["תאריך"] = _heatmap_parse_dates(df["תאריך"])
    df["YearMonth"] = df["תאריך"].dt.strftime("%Y-%m")

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
        source_path=csv_path,
    )


def get_bundle() -> HeatmapBundle | None:
    from web_control.totals_sheet_sync import ensure_totals_csv_present

    path = config.web_totals_file
    if not (os.path.isfile(path) and os.path.getsize(path) > 0):
        ok, err = ensure_totals_csv_present()
        if not ok:
            log.warning("heatmap: no local totals and cloud fetch failed: %s", err)
            return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    c = _bundle_cache
    if c["path"] == path and c["mtime"] == st.st_mtime and c["bundle"] is not None:
        return c["bundle"]
    try:
        bundle = _build_bundle(path)
    except Exception:  # noqa: BLE001
        log.exception("heatmap: failed to load %s", path)
        return None
    c["path"] = path
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
    row_totals = [_format_cell_money(float(pivot.loc[mi].sum())) for mi in pivot.index]
    return {
        "title": title,
        "reportType": report_type,
        "months": months,
        "categories": categories,
        "labels": labels,
        "cellBg": colors,
        "clickable": clickable,
        "columnTotals": column_totals,
        "rowTotals": row_totals,
    }


def api_snapshot() -> dict[str, Any]:
    from web_control.totals_sheet_sync import local_totals_status

    bundle = get_bundle()
    source_status = local_totals_status()
    if bundle is None:
        msg = f"Could not load heatmap data from {config.web_totals_file}."
        if not source_status["configured"]:
            msg += " Set GOOGLE_API_USER (service account JSON path) and GOOGLE_WORKSHEET_ID, then use Refresh from Google Sheet."
        elif not source_status["local_exists"]:
            msg += " Cloud fetch on load failed — check credentials and the sheet tab name."
        else:
            msg += " File exists but could not be parsed (check CSV columns)."
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


def detail_page_html(bundle: HeatmapBundle, report_type: ReportType, year_month: str, category: str) -> str | None:
    """Return HTML body content or None if no matching slice."""
    cols_show_exp = ["תאריך", "מקור עסקה", "בחובה", "תאור מורחב", "פירוט נוסף"]
    cols_show_in = ["תאריך", "מקור עסקה", "בזכות", "תאור מורחב", "פירוט נוסף"]
    df = bundle.df
    if report_type == "expense":
        pivot = bundle.expenses_pivot
        if year_month not in pivot.index or category not in pivot.columns:
            return None
        if pivot.loc[year_month, category] <= 0:
            return None
        mask = (
            (df["YearMonth"] == year_month)
            & (df["קטגוריה"] == category)
            & (df["בחובה"] > 0)
        )
        details = df.loc[mask, cols_show_exp]
        title = f"פירוט הוצאות עבור {html.escape(category)} ב-{html.escape(year_month)}"
        table = details.to_html(index=False, classes="styled-table", float_format="%.2f")
        inner = f"<h1>{title}</h1>{table}"
    elif report_type == "income":
        pivot = bundle.income_pivot
        if year_month not in pivot.index or category not in pivot.columns:
            return None
        if pivot.loc[year_month, category] <= 0:
            return None
        mask = (
            (df["YearMonth"] == year_month)
            & (df["קטגוריה"] == category)
            & (df["בזכות"] > 0)
        )
        details = df.loc[mask, cols_show_in]
        title = f"פירוט הכנסות עבור {html.escape(category)} ב-{html.escape(year_month)}"
        table = details.to_html(index=False, classes="styled-table", float_format="%.2f")
        inner = f"<h1>{title}</h1>{table}"
    else:
        pivot = bundle.net_pivot
        if year_month not in pivot.index or category not in pivot.columns:
            return None
        if pivot.loc[year_month, category] == 0:
            return None
        income_mask = (
            (df["YearMonth"] == year_month)
            & (df["קטגוריה"] == category)
            & (df["בזכות"] > 0)
        )
        expense_mask = (
            (df["YearMonth"] == year_month)
            & (df["קטגוריה"] == category)
            & (df["בחובה"] > 0)
        )
        income_df = df.loc[income_mask, cols_show_in]
        expense_df = df.loc[expense_mask, cols_show_exp]
        income_html = (
            income_df.to_html(index=False, classes="styled-table", float_format="%.2f")
            if not income_df.empty
            else "<p class='no-data'>אין הכנסות רשומות</p>"
        )
        expense_html = (
            expense_df.to_html(index=False, classes="styled-table", float_format="%.2f")
            if not expense_df.empty
            else "<p class='no-data'>אין הוצאות רשומות</p>"
        )
        title = f"פירוט תנועות עבור {html.escape(category)} ב-{html.escape(year_month)}"
        inner = f"<h1>{title}</h1><h2>הכנסות</h2>{income_html}<h2>הוצאות</h2>{expense_html}"

    return _wrap_detail_document(inner)


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
      .styled-table thead th { background: #2d4a2f; color: #e8f5e9; }
      .styled-table tbody tr:hover { background: #1e1f24 !important; }
      .tabs { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.5rem 0 1rem; }
      .tabs button {
        font: inherit; cursor: pointer; padding: 0.4rem 0.75rem; border-radius: 8px;
        border: 1px solid #3a3b44; background: #1c1d22; color: #c8cad4;
      }
      .tabs button.active { border-color: #4c6ef5; background: #2a2f4a; color: #e8e8ec; }
      .heatmap-title { font-size: 1.1rem; font-weight: 600; margin: 0.25rem 0 0.75rem; }
      .heatmap-wrap { overflow: auto; max-width: 100%; margin-bottom: 1.5rem;
        border: 1px solid #2b2c33; border-radius: 8px; background: #0b0c0f; }
      table.hm-grid { border-collapse: separate; border-spacing: 1px;
        font-size: 0.72rem; margin: 0; }
      table.hm-grid th, table.hm-grid td {
        min-width: 3.2rem; max-width: 8rem; padding: 4px 5px; text-align: center;
        vertical-align: middle; word-break: break-word;
      }
      table.hm-grid th { background: #1c1d22; color: #aeb4c0; font-weight: 600;
        position: sticky; top: 0; z-index: 2; }
      table.hm-grid th.row-h { position: sticky; left: 0; z-index: 3; background: #16171c; }
      table.hm-grid th.corner { z-index: 4; left: 0; top: 0; background: #14151a; }
      table.hm-grid thead th.hm-colsum {
        top: 0; z-index: 3; background: #25262e; color: #dce0ea; font-size: 0.68rem;
        font-weight: 600; border-bottom: 1px solid #3a3b44;
      }
      table.hm-grid thead tr:nth-child(2) th {
        top: 2.35rem; z-index: 2; background: #1c1d22;
      }
      table.hm-grid thead th.hm-rowsum-h {
        top: 0; left: 4.5rem; z-index: 5; background: #1a1b20; color: #c4c8d4;
        min-width: 3.6rem; vertical-align: middle; line-height: 1.2;
      }
      table.hm-grid tbody td.hm-rowtot {
        position: sticky; left: 4.5rem; z-index: 3; background: #1a1b20; color: #dce0ea;
        font-weight: 600; font-size: 0.7rem; border: 1px solid #2b2c33;
      }
      table.hm-grid tbody th.row-h { z-index: 4; }
      table.hm-grid td.cell {
        cursor: default; color: #0d0d0f; font-weight: 600; text-shadow: 0 0 2px rgba(255,255,255,0.35);
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


def heatmap_shell_html() -> str:
    return (
        f"""<!DOCTYPE html>
<html lang="he">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>מפת חום — Heatmap</title>
  {_heatmap_shared_css()}
</head>
<body>
  {control_nav.control_topnav_html()}
  <h1>מפת חום — הוצאות / הכנסות / נטו</h1>
  <p class="subtle">מקור אמת: גיליון Google <strong>Totals</strong> — רשומות מלאות בטאב אחד (לא מחולק לפי שנה). נשמר מקומית ב־<code>web/data/web_totals.csv</code>; שם הטאב ניתן לשינוי ב־<code>FINANCE_TOTALS_SHEET_NAME</code>. לחיצה על תא פותחת פירוט תנועות.</p>
  <div class="heatmap-toolbar">
    <button type="button" id="btn-refresh">Refresh from Google Sheet</button>
    <span id="refresh-status"></span>
  </div>
  <div id="err" class="err-banner" style="display:none"></div>
  <div class="tabs" id="tabs">
    <button type="button" data-view="expense">הוצאות</button>
    <button type="button" data-view="income">הכנסות</button>
    <button type="button" data-view="net">נטו</button>
  </div>
  <div id="heatmap-title" class="heatmap-title"></div>
  <div class="heatmap-wrap"><div id="grid"></div></div>
  <div class="stats-container">
    <div class="stats-table-container">
      <h2>סיכום לפי קטגוריה</h2>
      <div id="stats-cat"></div>
    </div>
    <div class="stats-table-container">
      <h2>סיכום לפי חודש</h2>
      <div id="stats-month"></div>
    </div>
  </div>
  <script src="/heatmap/heatmap_page_script.js" defer></script>
</body>
</html>
"""
    )


def handle_detail_query(query: str) -> tuple[int, bytes, str]:
    qs = parse_qs(query, keep_blank_values=True)
    rt = (qs.get("type") or ["expense"])[0].strip().lower()
    if rt not in ("expense", "income", "net"):
        return 400, b"Invalid type", "text/plain; charset=utf-8"
    ym = (qs.get("ym") or [""])[0].strip()
    cat = (qs.get("cat") or [""])[0]
    if not ym or not cat:
        return 400, b"ym and cat required", "text/plain; charset=utf-8"
    bundle = get_bundle()
    if bundle is None:
        return 503, b"Data not available", "text/plain; charset=utf-8"
    page = detail_page_html(bundle, rt, ym, cat)  # type: ignore[arg-type]
    if page is None:
        return 404, b"Not found", "text/plain; charset=utf-8"
    return 200, page.encode("utf-8"), "text/html; charset=utf-8"
