"""HTTP handlers for the prediction explorer (sample + live score / tweak)."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs

from ledger.category_model import (
    CategorySuggestion,
    Embedder,
    ModelMeta,
    TxFeatureRow,
    _combine_details,
    _as_float,
    _as_text,
    load_model_meta,
    load_trained_model,
    predict_suggestions,
    suggestions_to_dicts,
    try_load_minilm_embedder,
)
from ledger.store import ledger_connect_readonly

log = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_model_cache: Optional[tuple[str, Any, ModelMeta, Optional[Any], Optional[Embedder]]] = None
_train_lock = threading.Lock()


def _ledger_path() -> str:
    from ledger.books import active_ledger_path

    return active_ledger_path()


def _clear_model_cache() -> None:
    global _model_cache
    with _cache_lock:
        _model_cache = None


def _parse_int(raw: Optional[str], default: int, *, lo: int, hi: int) -> int:
    try:
        n = int(raw) if raw is not None and str(raw).strip() != "" else default
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _parse_bool(raw: Optional[str], default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _load_live_bundle() -> Optional[tuple[Any, ModelMeta, Optional[Any], Optional[Embedder]]]:
    """Cached CatBoost + meta + centroids + optional MiniLM embedder."""
    global _model_cache
    meta = load_model_meta()
    if meta is None:
        return None
    import config

    cbm = config.category_catboost_model_file
    if not os.path.isfile(cbm):
        return None
    try:
        mtime = str(os.path.getmtime(cbm))
    except OSError:
        mtime = ""
    cache_key = f"{meta.model_version}:{mtime}:{meta.has_embeddings}"
    with _cache_lock:
        if _model_cache is not None and _model_cache[0] == cache_key:
            return _model_cache[1], _model_cache[2], _model_cache[3], _model_cache[4]
    packed = load_trained_model()
    if packed is None:
        return None
    model, loaded_meta, centroids = packed
    embedder: Optional[Embedder] = None
    if loaded_meta.has_embeddings:
        embedder = try_load_minilm_embedder()
    with _cache_lock:
        _model_cache = (cache_key, model, loaded_meta, centroids, embedder)
    return model, loaded_meta, centroids, embedder


def _score_rows(
    rows: list[TxFeatureRow],
    *,
    top_n: int = 3,
    min_confidence: float = 0.0,
) -> tuple[Optional[ModelMeta], list[list[CategorySuggestion]], Optional[str]]:
    bundle = _load_live_bundle()
    if bundle is None:
        return None, [[] for _ in rows], "model_not_ready"
    model, meta, centroids, embedder = bundle
    scored = predict_suggestions(
        model,
        meta,
        rows,
        centroids=centroids,
        embedder=embedder if meta.has_embeddings else None,
        min_confidence=min_confidence,
        top_n=top_n,
    )
    return meta, scored, None


def handle_status() -> tuple[int, dict[str, Any]]:
    path = _ledger_path()
    meta = load_model_meta()
    import config

    cbm_exists = os.path.isfile(config.category_catboost_model_file)
    minilm_dir = os.path.isdir(config.minilm_cache_dir)
    labeled = 0
    awaiting = 0
    if os.path.isfile(path):
        conn = ledger_connect_readonly(path)
        try:
            labeled = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM ledger_transaction
                    WHERE TRIM(COALESCE("קטגוריה", '')) NOT IN ('', 'awaiting')
                      AND COALESCE(excluded_from_calculations, 0) = 0
                    """
                ).fetchone()[0]
            )
            awaiting = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM ledger_transaction
                    WHERE TRIM(COALESCE("קטגוריה", '')) IN ('', 'awaiting')
                      AND COALESCE(excluded_from_calculations, 0) = 0
                    """
                ).fetchone()[0]
            )
        finally:
            conn.close()
    ready = bool(meta is not None and cbm_exists)
    return 200, {
        "ok": True,
        "ledger_exists": os.path.isfile(path),
        "model_ready": ready,
        "model_version": meta.model_version if meta else None,
        "corpus_key": meta.corpus_key if meta else None,
        "has_embeddings": bool(meta.has_embeddings) if meta else False,
        "minilm_present": minilm_dir,
        "min_confidence": meta.min_confidence if meta else None,
        "labeled_count": labeled,
        "awaiting_count": awaiting,
        "class_count": len(meta.sim_categories) if meta and meta.has_embeddings else None,
    }


def handle_train() -> tuple[int, dict[str, Any]]:
    """Force-retrain CatBoost on all labeled rows and rescore uncategorized rows."""
    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {
            "ok": False,
            "error": "ledger_missing",
            "message": "ledger not found",
        }
    if not _train_lock.acquire(blocking=False):
        return 409, {
            "ok": False,
            "error": "train_in_progress",
            "message": "A train is already running.",
        }
    try:
        from ledger.category_suggest_job import run_category_suggest_refresh

        run_category_suggest_refresh(path, force_retrain=True)
        _clear_model_cache()
        code, status = handle_status()
        if not status.get("model_ready"):
            return 422, {
                "ok": False,
                "error": "train_insufficient_data",
                "message": "Need at least two categories with two or more labeled rows each.",
                **{k: status.get(k) for k in ("labeled_count", "awaiting_count", "minilm_present")},
            }
        return code, {
            "ok": True,
            "trained": True,
            "model_ready": status.get("model_ready"),
            "model_version": status.get("model_version"),
            "corpus_key": status.get("corpus_key"),
            "has_embeddings": status.get("has_embeddings"),
            "minilm_present": status.get("minilm_present"),
            "labeled_count": status.get("labeled_count"),
            "awaiting_count": status.get("awaiting_count"),
        }
    except Exception:
        log.exception("prediction train failed")
        return 500, {
            "ok": False,
            "error": "train_failed",
            "message": "Train failed (see server log).",
        }
    finally:
        _train_lock.release()


def handle_sample(query: str) -> tuple[int, dict[str, Any]]:
    qs = parse_qs(query or "", keep_blank_values=True)
    n = _parse_int((qs.get("n") or [None])[0], 20, lo=1, hi=100)
    labeled_only = _parse_bool((qs.get("labeled_only") or ["1"])[0], True)
    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "ledger_missing", "message": "ledger not found", "rows": []}

    cat_filter = (
        """TRIM(COALESCE("קטגוריה", '')) NOT IN ('', 'awaiting')"""
        if labeled_only
        else "1=1"
    )
    conn = ledger_connect_readonly(path)
    try:
        raw_rows = conn.execute(
            f"""
            SELECT fingerprint, "מקור עסקה", "תאור מורחב", "פירוט נוסף",
                   "בחובה", "בזכות", "קטגוריה", "תאריך"
            FROM ledger_transaction
            WHERE fingerprint IS NOT NULL
              AND length(trim(fingerprint)) > 0
              AND COALESCE(excluded_from_calculations, 0) = 0
              AND ({cat_filter})
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (n,),
        ).fetchall()
    finally:
        conn.close()

    feature_rows: list[TxFeatureRow] = []
    base: list[dict[str, Any]] = []
    for fp, payee, details, extra, expense, income, category, date in raw_rows:
        details_text = _combine_details(extra, details)
        feature_rows.append(
            TxFeatureRow(
                fingerprint=_as_text(fp),
                payee=_as_text(payee),
                details=details_text,
                outflow=_as_float(expense),
                inflow=_as_float(income),
                category=_as_text(category),
            )
        )
        base.append(
            {
                "fingerprint": _as_text(fp),
                "date": _as_text(date),
                "payee": _as_text(payee),
                "details": details_text,
                "outflow": _as_float(expense),
                "inflow": _as_float(income),
                "actual_category": _as_text(category),
            }
        )

    meta, scored, err = _score_rows(feature_rows, top_n=3, min_confidence=0.0)
    out_rows: list[dict[str, Any]] = []
    match_n = 0
    for item, preds in zip(base, scored):
        top = preds[0].category if preds else None
        actual = item["actual_category"]
        matched = bool(actual and top and actual == top)
        if matched:
            match_n += 1
        out_rows.append(
            {
                **item,
                "predictions": suggestions_to_dicts(preds),
                "top_category": top,
                "top_confidence_pct": preds[0].confidence_pct if preds else None,
                "match": matched if actual else None,
            }
        )

    labeled_scored = sum(1 for r in out_rows if r.get("actual_category"))
    return 200, {
        "ok": True,
        "model_ready": meta is not None,
        "model_version": meta.model_version if meta else None,
        "error": err,
        "n": len(out_rows),
        "match_count": match_n,
        "labeled_scored": labeled_scored,
        "match_rate_pct": round(100.0 * match_n / labeled_scored, 1) if labeled_scored else None,
        "rows": out_rows,
    }


def handle_row(query: str) -> tuple[int, dict[str, Any]]:
    qs = parse_qs(query or "", keep_blank_values=True)
    fp = _as_text((qs.get("fingerprint") or [""])[0])
    if not fp:
        return 400, {"ok": False, "error": "fingerprint_required", "message": "fingerprint required"}
    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "ledger_missing", "message": "ledger not found"}
    conn = ledger_connect_readonly(path)
    try:
        row = conn.execute(
            """
            SELECT fingerprint, "מקור עסקה", "תאור מורחב", "פירוט נוסף",
                   "בחובה", "בזכות", "קטגוריה", "תאריך"
            FROM ledger_transaction
            WHERE fingerprint = ?
            LIMIT 1
            """,
            (fp,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return 404, {"ok": False, "error": "not_found", "message": "row not found"}
    fp0, payee, details, extra, expense, income, category, date = row
    details_text = _combine_details(extra, details)
    return 200, {
        "ok": True,
        "row": {
            "fingerprint": _as_text(fp0),
            "date": _as_text(date),
            "payee": _as_text(payee),
            "details": details_text,
            "outflow": _as_float(expense),
            "inflow": _as_float(income),
            "actual_category": _as_text(category),
        },
    }


def handle_score(body: Mapping[str, Any] | bytes | str | None) -> tuple[int, dict[str, Any]]:
    if isinstance(body, (bytes, bytearray)):
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, {"ok": False, "error": "invalid_json", "message": "invalid JSON body"}
    elif isinstance(body, str):
        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError:
            return 400, {"ok": False, "error": "invalid_json", "message": "invalid JSON body"}
    elif isinstance(body, Mapping):
        data = body
    else:
        data = {}

    payee = _as_text(data.get("payee") or data.get("מקור עסקה"))
    details = _as_text(data.get("details"))
    if not details:
        details = _combine_details(data.get("פירוט נוסף"), data.get("תאור מורחב"))
    outflow = _as_float(data.get("outflow") if "outflow" in data else data.get("בחובה"))
    inflow = _as_float(data.get("inflow") if "inflow" in data else data.get("בזכות"))
    top_n = _parse_int(str(data.get("top_n") or "5"), 5, lo=1, hi=15)

    row = TxFeatureRow(
        fingerprint=_as_text(data.get("fingerprint")),
        payee=payee,
        details=details,
        outflow=outflow,
        inflow=inflow,
        category="",
    )
    meta, scored, err = _score_rows([row], top_n=top_n, min_confidence=0.0)
    if err == "model_not_ready":
        return 503, {
            "ok": False,
            "error": "model_not_ready",
            "message": "Train the category model first (run the transactions pipeline).",
            "predictions": [],
        }
    preds = scored[0] if scored else []
    return 200, {
        "ok": True,
        "model_version": meta.model_version if meta else None,
        "has_embeddings": bool(meta.has_embeddings) if meta else False,
        "features": {
            "payee": payee,
            "details": details,
            "outflow": outflow,
            "inflow": inflow,
        },
        "predictions": suggestions_to_dicts(preds),
        "top_category": preds[0].category if preds else None,
        "top_confidence_pct": preds[0].confidence_pct if preds else None,
    }
