"""Canonical transaction dedupe strings for ``ledger_transaction.fingerprint``."""

from __future__ import annotations

import logging
import math
import re
from typing import Any

import pandas as pd

from pipeline.compiler import parse_post_ingest_date_scalar

log = logging.getLogger(__name__)


def _finite_money(val: Any) -> float | None:
    """Finite ILS amount for the dedupe key.

    Missing / empty → ``0.0`` (same as historic ``float(x or 0)``).
    Non-finite (NaN, Inf) → ``None`` so the caller skips the row instead of
    emitting ``bhnan_…`` keys that UNIQUE-merge unrelated transactions.
    """
    if val is None:
        return 0.0
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return 0.0
        val = s
    try:
        if val is pd.NA:
            return 0.0
    except (ValueError, TypeError):
        pass
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return None
    return n


def _fingerprint_optional_fragment(row, key: str) -> str:
    """
    Text contribution for ``פירוט נוסף`` / ``תאור מורחב`` in the dedupe key.

    Missing columns, ``None``, ``NaN``, ``pd.NA``, and empty/whitespace all normalize to ``""``
    so the same logical row does not get different fingerprints from ``str(None)`` vs ``str(nan)``.
    """
    try:
        if isinstance(row, pd.Series):
            if key not in row.index:
                return ""
        elif key not in row:
            return ""
        val = row[key]
    except (KeyError, TypeError):
        return ""
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except TypeError:
        pass
    s = str(val).strip().lower()
    return s


def generate_transaction_fingerprint_legacy(row):
    """Pre–schema-v10 fingerprint: single signed amount (debit OR credit), so opposite flows could collide."""
    try:
        ts = parse_post_ingest_date_scalar(row["תאריך"])
        if pd.isna(ts):
            return None
        normalized_date = ts.strftime("%Y-%m-%d")
        raw_amount = row["בחובה"] if row["בחובה"] != 0 else row["בזכות"]
        amount = _finite_money(raw_amount)
        if amount is None:
            return None
        normalized_amount = f"{amount:.2f}"
        business_name = str(row["מקור עסקה"]).lower().strip()
        business_name = re.sub(r"[^a-z0-9\u0590-\u05ff]", "", business_name)
        extract_data = _fingerprint_optional_fragment(row, "פירוט נוסף") + _fingerprint_optional_fragment(
            row, "תאור מורחב"
        )

        extra_data = re.sub(r"[^a-z0-9\u0590-\u05ff]", "", extract_data)
        fingerprint_key = f"{normalized_date}:{normalized_amount}:{business_name}:{extra_data}"
        log.debug("Fingerprint legacy key: %s", fingerprint_key)
        return fingerprint_key

    except (ValueError, TypeError):
        return None


def generate_transaction_fingerprint(row):
    """Canonical ledger dedupe key: encodes **both** ``בחובה`` and ``בזכות`` (``bh{X.XX}_bz{Y.YY}``)."""
    try:
        ts = parse_post_ingest_date_scalar(row["תאריך"])
        if pd.isna(ts):
            return None
        normalized_date = ts.strftime("%Y-%m-%d")
        bh = _finite_money(row.get("בחובה"))
        bz = _finite_money(row.get("בזכות"))
        if bh is None or bz is None:
            return None
        normalized_amount = f"bh{bh:.2f}_bz{bz:.2f}"
        business_name = str(row["מקור עסקה"]).lower().strip()
        business_name = re.sub(r"[^a-z0-9\u0590-\u05ff]", "", business_name)
        extract_data = _fingerprint_optional_fragment(row, "פירוט נוסף") + _fingerprint_optional_fragment(
            row, "תאור מורחב"
        )

        extra_data = re.sub(r"[^a-z0-9\u0590-\u05ff]", "", extract_data)
        fingerprint_key = f"{normalized_date}:{normalized_amount}:{business_name}:{extra_data}"
        log.debug("Fingerprint key: %s", fingerprint_key)
        return fingerprint_key

    except (ValueError, TypeError):
        return None


# Back-compat alias — same as :func:`generate_transaction_fingerprint`.
generate_transaction_fingerprint_v2 = generate_transaction_fingerprint
