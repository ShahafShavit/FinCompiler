"""Canonical transaction dedupe strings for ``ledger_transaction.fingerprint``."""

from __future__ import annotations

import logging
import re

import pandas as pd

from pipeline.compiler import parse_post_ingest_date_scalar

log = logging.getLogger(__name__)


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
        amount = row["בחובה"] if row["בחובה"] != 0 else row["בזכות"]
        normalized_amount = f"{float(amount):.2f}"
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
        bh = float(row.get("בחובה") or 0)
        bz = float(row.get("בזכות") or 0)
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
