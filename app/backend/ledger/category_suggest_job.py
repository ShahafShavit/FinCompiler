"""Train/score category suggestions: sync at ingest, coalesced background after saves."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional, Union

from ledger.category_model import (
    Embedder,
    corpus_key_from_conn,
    load_labeled_feature_rows,
    load_model_meta,
    load_trained_model,
    load_uncategorized_feature_rows,
    predict_suggestions,
    replace_suggestions_for_fingerprints,
    train_category_model,
    try_load_minilm_embedder,
)
from ledger.store import ledger_connect_readonly

log = logging.getLogger(__name__)

_UNSET: Any = object()

_state_lock = threading.Lock()
_running = False
_rerun = False
_active_db_path: Optional[str] = None


def run_category_suggest_refresh(
    db_path: str,
    *,
    embedder: Union[Embedder, None, Any] = _UNSET,
    iterations: int = 80,
    force_retrain: bool = False,
) -> None:
    """Train when the labeled corpus key changed, then score uncategorized rows.

    Does not write ``קטגוריה``. Safe to call from ingest (blocking) or a worker thread.

    Pass ``embedder=None`` to force text+amount features only (no MiniLM load).
    Omit ``embedder`` to load MiniLM from ``data/models/minilm`` when present.
    """
    if not db_path or not os.path.isfile(db_path):
        log.warning("category suggest refresh skipped: missing ledger %s", db_path)
        return

    if embedder is _UNSET:
        resolved_embedder: Optional[Embedder] = try_load_minilm_embedder()
    else:
        resolved_embedder = embedder

    conn = ledger_connect_readonly(db_path)
    try:
        corpus_key = corpus_key_from_conn(conn)
        labeled = load_labeled_feature_rows(conn)
        awaiting = load_uncategorized_feature_rows(conn)
    finally:
        conn.close()

    meta = load_model_meta()
    need_train = force_retrain or meta is None or meta.corpus_key != corpus_key
    packed = None if need_train else load_trained_model()

    if need_train:
        log.info(
            "category suggest: training on %s labeled row(s) (corpus_key=%s)",
            len(labeled),
            corpus_key,
        )
        packed = train_category_model(
            labeled,
            corpus_key=corpus_key,
            embedder=resolved_embedder,
            iterations=iterations,
        )
        if packed is None:
            log.info("category suggest: training skipped (insufficient classes)")
            return
    elif packed is None:
        log.info("category suggest: model files missing; forcing retrain")
        packed = train_category_model(
            labeled,
            corpus_key=corpus_key,
            embedder=resolved_embedder,
            iterations=iterations,
        )
        if packed is None:
            return

    model, meta, centroids = packed
    use_embedder = resolved_embedder if meta.has_embeddings else None

    if not awaiting:
        replace_suggestions_for_fingerprints(db_path, {}, model_version=meta.model_version)
        log.info("category suggest: no uncategorized rows to score")
        return

    # Score only fingerprints that lack the current model_version.
    conn = ledger_connect_readonly(db_path)
    try:
        existing = {
            str(r[0]): str(r[1])
            for r in conn.execute(
                "SELECT fingerprint, model_version FROM category_suggestion"
            ).fetchall()
        }
    finally:
        conn.close()

    to_score = [
        row
        for row in awaiting
        if existing.get(row.fingerprint) != meta.model_version
    ]
    if not to_score:
        log.info("category suggest: all %s awaiting row(s) already scored", len(awaiting))
        return

    log.info(
        "category suggest: scoring %s of %s uncategorized row(s)",
        len(to_score),
        len(awaiting),
    )
    suggestions = predict_suggestions(
        model,
        meta,
        to_score,
        centroids=centroids,
        embedder=use_embedder,
    )
    scored = {row.fingerprint: sugg for row, sugg in zip(to_score, suggestions)}
    replace_suggestions_for_fingerprints(db_path, scored, model_version=meta.model_version)


def schedule_category_suggest_refresh(db_path: str) -> None:
    """Start a background refresh. Overlapping saves coalesce into one follow-up run."""
    global _running, _rerun, _active_db_path

    path = str(db_path or "").strip()
    if not path:
        return

    with _state_lock:
        _active_db_path = path
        if _running:
            _rerun = True
            return
        _running = True

    def worker() -> None:
        global _running, _rerun, _active_db_path
        try:
            while True:
                current = _active_db_path or path
                try:
                    run_category_suggest_refresh(current)
                except Exception:
                    log.exception("category suggest background refresh failed")
                with _state_lock:
                    if _rerun:
                        _rerun = False
                        continue
                    _running = False
                    return
        except Exception:
            log.exception("category suggest worker crashed")
            with _state_lock:
                _running = False
                _rerun = False

    threading.Thread(
        target=worker,
        name="category-suggest-refresh",
        daemon=True,
    ).start()
