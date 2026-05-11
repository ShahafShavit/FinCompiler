import datetime
import logging
import re

import pandas as pd
import pandas.errors

log = logging.getLogger(__name__)

_ISO_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _standardize_date_separators(val: object) -> str | None:
    """Normalize ``-``/``.``/``/`` for parsing; return None if missing."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except TypeError:
        pass
    # ``str(Timestamp)`` includes time — would miss the ISO branch and hit dayfirst/mixed (buggy on ISO).
    if isinstance(val, datetime.date):
        return pd.Timestamp(val).strftime("%Y-%m-%d")
    s = str(val).strip()
    if s.lower() in ("nan", "nat", "none", "<na>"):
        return None
    s = re.sub(r"[-/.]", "-", s)
    # ISO date at start of a datetime string (Excel / ``str(Timestamp)`` round-trip)
    if len(s) >= 10 and _ISO_YMD.fullmatch(s[:10]):
        return s[:10]
    return s


def parse_post_ingest_date_scalar(val: object) -> pd.Timestamp:
    """
    Parse a single ``תאריך`` cell after ingest / ledger round-trip (see :func:`parse_post_ingest_date_column`).
    """
    s = _standardize_date_separators(val)
    if s is None:
        return pd.NaT
    if _ISO_YMD.fullmatch(s):
        return pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    return pd.to_datetime(s, dayfirst=True, errors="coerce", format="mixed")


def parse_post_ingest_date_column(series: pd.Series) -> pd.Series:
    """
    Parse ``תאריך`` after ingest / ledger round-trip.

    Rows produced by this app are written as ISO dates (``datetime.date`` / ``%Y-%m-%d``).
    For those, parse with a fixed format — **never** pass ``dayfirst=True``, which on
    some pandas versions misreads ISO strings when combined with ``format=\"mixed\"``.

    Non-ISO strings (first ingest from bank Excel, legacy files) fall back to
    ``dayfirst=True`` + ``format=\"mixed\"`` like the old single-path behaviour.
    """
    return series.map(parse_post_ingest_date_scalar)


def normalize_transaction_import_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardize ``תאריך`` on a **new-import** transaction frame (same rules as legacy ``__compile_new__``).
    """
    if df.empty or "תאריך" not in df.columns:
        return df

    def standardize_date_format(date_str: object) -> str:
        return re.sub(r"[-/.]", "-", str(date_str))

    out = df.copy()
    out["תאריך"] = out["תאריך"].apply(standardize_date_format)
    out["תאריך"] = parse_post_ingest_date_column(out["תאריך"])
    out["תאריך"] = out["תאריך"].dt.date
    out.sort_values(by="תאריך", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out
