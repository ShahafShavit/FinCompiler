"""kNN-style category suggestions from categorized ledger history."""

from __future__ import annotations

import difflib
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Union

import pandas as pd

from ledger.store import ledger_connect_readonly

_TEXT_KEEP_RE = re.compile(r"[^a-z0-9\u0590-\u05ff]+")
_TOKEN_SPLIT_RE = re.compile(r"\s+")

_WEIGHT_PAYEE = 0.5
_WEIGHT_DESC = 0.3
_WEIGHT_AMOUNT = 0.2

_DEFAULT_K = 15
_DEFAULT_TOP_N = 3
_DEFAULT_MIN_CONFIDENCE = 0.35

_cache_lock = threading.Lock()
_corpus_cache: dict[str, tuple[str, list["CorpusRow"]]] = {}


@dataclass(frozen=True)
class CorpusRow:
    fingerprint: str
    payee: str
    description: str
    amount: float
    direction: str
    category: str
    payee_norm: str
    desc_tokens: frozenset[str]


@dataclass(frozen=True)
class CategorySuggestion:
    category: str
    confidence_pct: float


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    s = str(value).strip().lower()
    return _TEXT_KEEP_RE.sub("", s)


def _description_text(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("פירוט נוסף", "תאור מורחב"):
        val = row.get(key)
        if val is None:
            continue
        try:
            if pd.isna(val):
                continue
        except TypeError:
            pass
        t = str(val).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def _tokenize_description(text: str) -> frozenset[str]:
    norm = _normalize_text(text)
    if not norm:
        return frozenset()
    raw = _TEXT_KEEP_RE.sub(" ", norm)
    tokens = {t for t in _TOKEN_SPLIT_RE.split(raw.strip()) if len(t) >= 2}
    if not tokens and norm:
        tokens = {norm}
    return frozenset(tokens)


def _amount_and_direction(row: Mapping[str, Any]) -> tuple[float, str]:
    try:
        expense = float(row.get("בחובה") or 0)
    except (TypeError, ValueError):
        expense = 0.0
    try:
        income = float(row.get("בזכות") or 0)
    except (TypeError, ValueError):
        income = 0.0
    if abs(expense) > 1e-12 and abs(income) > 1e-12:
        return max(abs(expense), abs(income)), "both"
    if abs(expense) > 1e-12:
        return abs(expense), "expense"
    if abs(income) > 1e-12:
        return abs(income), "income"
    return 0.0, "none"


def _amount_bucket(amount: float) -> float:
    if amount <= 0:
        return 0.0
    if amount < 50:
        return round(amount, 0)
    if amount < 500:
        return round(amount / 10.0) * 10.0
    if amount < 5000:
        return round(amount / 50.0) * 50.0
    return round(amount / 100.0) * 100.0


def _payee_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _amount_similarity(
    amount_a: float,
    dir_a: str,
    amount_b: float,
    dir_b: str,
) -> float:
    if dir_a != dir_b:
        if dir_a == "both" or dir_b == "both":
            return 0.35
        return 0.0
    bucket_a = _amount_bucket(amount_a)
    bucket_b = _amount_bucket(amount_b)
    if bucket_a == bucket_b:
        return 1.0
    hi = max(bucket_a, bucket_b)
    lo = min(bucket_a, bucket_b)
    if hi <= 0:
        return 1.0 if lo <= 0 else 0.0
    ratio = lo / hi
    if ratio >= 0.85:
        return 0.85
    if ratio >= 0.5:
        return 0.55
    return 0.2


def _row_similarity(query: CorpusRow, neighbor: CorpusRow) -> float:
    payee_sim = _payee_similarity(query.payee_norm, neighbor.payee_norm)
    desc_sim = _jaccard(query.desc_tokens, neighbor.desc_tokens)
    amt_sim = _amount_similarity(query.amount, query.direction, neighbor.amount, neighbor.direction)
    return _WEIGHT_PAYEE * payee_sim + _WEIGHT_DESC * desc_sim + _WEIGHT_AMOUNT * amt_sim


def _corpus_row_from_mapping(row: Mapping[str, Any]) -> Optional[CorpusRow]:
    category = str(row.get("קטגוריה") or "").strip()
    if not category or category.lower() == "awaiting":
        return None
    fp = str(row.get("fingerprint") or "").strip()
    payee = str(row.get("מקור עסקה") or "").strip()
    desc = _description_text(row)
    amount, direction = _amount_and_direction(row)
    return CorpusRow(
        fingerprint=fp,
        payee=payee,
        description=desc,
        amount=amount,
        direction=direction,
        category=category,
        payee_norm=_normalize_text(payee),
        desc_tokens=_tokenize_description(desc),
    )


def _query_row_from_mapping(row: Union[pd.Series, Mapping[str, Any]]) -> CorpusRow:
    if isinstance(row, pd.Series):
        mapping: Mapping[str, Any] = row.to_dict()
    else:
        mapping = row
    payee = str(mapping.get("מקור עסקה") or "").strip()
    desc = _description_text(mapping)
    amount, direction = _amount_and_direction(mapping)
    fp = str(mapping.get("fingerprint") or "").strip()
    return CorpusRow(
        fingerprint=fp,
        payee=payee,
        description=desc,
        amount=amount,
        direction=direction,
        category="",
        payee_norm=_normalize_text(payee),
        desc_tokens=_tokenize_description(desc),
    )


def _corpus_cache_key(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(category_updated_at), ''), COUNT(*)
        FROM ledger_transaction
        WHERE TRIM(COALESCE("קטגוריה", '')) NOT IN ('', 'awaiting')
          AND COALESCE(excluded_from_calculations, 0) = 0
        """
    ).fetchone()
    if row is None:
        return "0:0"
    return f"{row[0]}:{row[1]}"


def load_categorized_corpus(conn: sqlite3.Connection) -> list[CorpusRow]:
    """Load labeled transactions used as the suggestion training set."""
    rows = conn.execute(
        """
        SELECT fingerprint, "מקור עסקה", "תאור מורחב", "פירוט נוסף",
               "בחובה", "בזכות", "קטגוריה"
        FROM ledger_transaction
        WHERE TRIM(COALESCE("קטגוריה", '')) NOT IN ('', 'awaiting')
          AND COALESCE(excluded_from_calculations, 0) = 0
        """
    ).fetchall()
    out: list[CorpusRow] = []
    for fp, payee, details, extra, expense, income, category in rows:
        mapped = _corpus_row_from_mapping(
            {
                "fingerprint": fp,
                "מקור עסקה": payee,
                "תאור מורחב": details,
                "פירוט נוסף": extra,
                "בחובה": expense,
                "בזכות": income,
                "קטגוריה": category,
            }
        )
        if mapped is not None:
            out.append(mapped)
    return out


def _load_corpus_cached(db_path: str) -> list[CorpusRow]:
    conn = ledger_connect_readonly(db_path)
    try:
        cache_key = _corpus_cache_key(conn)
        with _cache_lock:
            cached = _corpus_cache.get(db_path)
            if cached is not None and cached[0] == cache_key:
                return cached[1]
        corpus = load_categorized_corpus(conn)
        with _cache_lock:
            _corpus_cache[db_path] = (cache_key, corpus)
        return corpus
    finally:
        conn.close()


def suggest_categories(
    query_row: Union[pd.Series, Mapping[str, Any]],
    corpus: Sequence[CorpusRow],
    *,
    k: int = _DEFAULT_K,
    top_n: int = _DEFAULT_TOP_N,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> list[CategorySuggestion]:
    """Return top category suggestions with vote-share confidence among k neighbors."""
    if not corpus:
        return []
    query = _query_row_from_mapping(query_row)
    exclude_fp = query.fingerprint
    scored: list[tuple[float, str]] = []
    for neighbor in corpus:
        if exclude_fp and neighbor.fingerprint == exclude_fp:
            continue
        sim = _row_similarity(query, neighbor)
        if sim <= 0:
            continue
        scored.append((sim, neighbor.category))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    neighbors = scored[: max(1, k)]
    total_weight = sum(w for w, _ in neighbors)
    if total_weight <= 0:
        return []
    votes: dict[str, float] = {}
    for weight, category in neighbors:
        votes[category] = votes.get(category, 0.0) + weight
    ranked = sorted(votes.items(), key=lambda x: x[1], reverse=True)
    top_weight = ranked[0][1]
    if top_weight / total_weight < min_confidence:
        return []
    out: list[CategorySuggestion] = []
    for category, weight in ranked[: max(1, top_n)]:
        out.append(
            CategorySuggestion(
                category=category,
                confidence_pct=round(100.0 * weight / total_weight, 1),
            )
        )
    return out


def suggest_categories_for_row(
    db_path: str,
    query_row: Union[pd.Series, Mapping[str, Any]],
    *,
    k: int = _DEFAULT_K,
    top_n: int = _DEFAULT_TOP_N,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> list[CategorySuggestion]:
    """Load corpus from ledger and suggest categories for one transaction row."""
    corpus = _load_corpus_cached(db_path)
    return suggest_categories(
        query_row,
        corpus,
        k=k,
        top_n=top_n,
        min_confidence=min_confidence,
    )
