"""Local CatBoost category model: text + amounts + optional MiniLM centroid similarities."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Sequence, Union

import numpy as np
import pandas as pd

from ledger.store import connect_ledger, ledger_connect_readonly

log = logging.getLogger(__name__)

MINILM_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_DEFAULT_TOP_N = 3
_DEFAULT_MIN_CONFIDENCE = 0.35
_MIN_CLASS_EXAMPLES = 2

_TEXT_PROCESSING = {
    "tokenizers": [
        {
            "tokenizer_id": "Space",
            "separator_type": "ByDelimiter",
            "delimiter": " ",
            "lowercasing": "true",
        }
    ],
    # Word bigrams are omitted: a single-token payee (e.g. "FluidShop") makes the
    # BiGram dictionary empty and CatBoost aborts training.
    "dictionaries": [
        {
            "dictionary_id": "Word",
            "token_level_type": "Word",
            "gram_order": "1",
            "occurrence_lower_bound": "1",
            "max_dictionary_size": "50000",
        },
        {
            "dictionary_id": "Char3",
            "token_level_type": "Letter",
            "gram_order": "3",
            "occurrence_lower_bound": "1",
            "max_dictionary_size": "20000",
        },
        {
            "dictionary_id": "Char4",
            "token_level_type": "Letter",
            "gram_order": "4",
            "occurrence_lower_bound": "1",
            "max_dictionary_size": "20000",
        },
    ],
    "feature_processing": {
        "default": [
            {
                "dictionaries_names": ["Word", "Char3", "Char4"],
                "feature_calcers": ["BoW"],
            }
        ]
    },
}


class Embedder(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Return shape (n, dim) float32 vectors for ``texts``."""


@dataclass(frozen=True)
class CategorySuggestion:
    category: str
    confidence_pct: float


@dataclass(frozen=True)
class ModelMeta:
    corpus_key: str
    sim_categories: tuple[str, ...]
    has_embeddings: bool
    model_version: str
    top_n: int = _DEFAULT_TOP_N
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE

    def to_json(self) -> dict[str, Any]:
        return {
            "corpus_key": self.corpus_key,
            "sim_categories": list(self.sim_categories),
            "has_embeddings": self.has_embeddings,
            "model_version": self.model_version,
            "top_n": self.top_n,
            "min_confidence": self.min_confidence,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ModelMeta":
        return cls(
            corpus_key=str(data.get("corpus_key") or ""),
            sim_categories=tuple(str(x) for x in (data.get("sim_categories") or [])),
            has_embeddings=bool(data.get("has_embeddings")),
            model_version=str(data.get("model_version") or ""),
            top_n=int(data.get("top_n") or _DEFAULT_TOP_N),
            min_confidence=float(data.get("min_confidence") or _DEFAULT_MIN_CONFIDENCE),
        )


@dataclass(frozen=True)
class TxFeatureRow:
    fingerprint: str
    payee: str
    details: str
    outflow: float
    inflow: float
    category: str


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value).strip()


def _as_float(value: Any) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _combine_details(extra: Any, details: Any) -> str:
    parts = [p for p in (_as_text(extra), _as_text(details)) if p]
    return " ".join(parts)


def _embedding_text(payee: str, details: str) -> str:
    return f"{payee} {details}".strip()


def corpus_key_from_conn(conn: sqlite3.Connection) -> str:
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


def load_labeled_feature_rows(conn: sqlite3.Connection) -> list[TxFeatureRow]:
    rows = conn.execute(
        """
        SELECT fingerprint, "מקור עסקה", "תאור מורחב", "פירוט נוסף",
               "בחובה", "בזכות", "קטגוריה"
        FROM ledger_transaction
        WHERE TRIM(COALESCE("קטגוריה", '')) NOT IN ('', 'awaiting')
          AND COALESCE(excluded_from_calculations, 0) = 0
          AND fingerprint IS NOT NULL
          AND length(trim(fingerprint)) > 0
        """
    ).fetchall()
    out: list[TxFeatureRow] = []
    for fp, payee, details, extra, expense, income, category in rows:
        cat = _as_text(category)
        if not cat:
            continue
        out.append(
            TxFeatureRow(
                fingerprint=_as_text(fp),
                payee=_as_text(payee),
                details=_combine_details(extra, details),
                outflow=_as_float(expense),
                inflow=_as_float(income),
                category=cat,
            )
        )
    return out


def load_uncategorized_feature_rows(conn: sqlite3.Connection) -> list[TxFeatureRow]:
    rows = conn.execute(
        """
        SELECT fingerprint, "מקור עסקה", "תאור מורחב", "פירוט נוסף",
               "בחובה", "בזכות"
        FROM ledger_transaction
        WHERE TRIM(COALESCE("קטגוריה", '')) IN ('', 'awaiting')
          AND COALESCE(excluded_from_calculations, 0) = 0
          AND fingerprint IS NOT NULL
          AND length(trim(fingerprint)) > 0
        """
    ).fetchall()
    out: list[TxFeatureRow] = []
    for fp, payee, details, extra, expense, income in rows:
        out.append(
            TxFeatureRow(
                fingerprint=_as_text(fp),
                payee=_as_text(payee),
                details=_combine_details(extra, details),
                outflow=_as_float(expense),
                inflow=_as_float(income),
                category="",
            )
        )
    return out


def feature_row_from_mapping(row: Union[pd.Series, Mapping[str, Any]]) -> TxFeatureRow:
    if isinstance(row, pd.Series):
        mapping: Mapping[str, Any] = row.to_dict()
    else:
        mapping = row
    return TxFeatureRow(
        fingerprint=_as_text(mapping.get("fingerprint")),
        payee=_as_text(mapping.get("מקור עסקה")),
        details=_combine_details(mapping.get("פירוט נוסף"), mapping.get("תאור מורחב")),
        outflow=_as_float(mapping.get("בחובה")),
        inflow=_as_float(mapping.get("בזכות")),
        category=_as_text(mapping.get("קטגוריה")),
    )


def _eligible_categories(labeled: Sequence[TxFeatureRow]) -> list[str]:
    counts: dict[str, int] = {}
    for row in labeled:
        counts[row.category] = counts.get(row.category, 0) + 1
    return sorted(c for c, n in counts.items() if n >= _MIN_CLASS_EXAMPLES)


def _cosine_sims(vectors: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """vectors (n, d), centroids (k, d) -> (n, k) cosine similarities."""
    if vectors.size == 0 or centroids.size == 0:
        return np.zeros((vectors.shape[0], centroids.shape[0]), dtype=np.float32)
    v = vectors.astype(np.float32, copy=False)
    c = centroids.astype(np.float32, copy=False)
    v_norm = np.linalg.norm(v, axis=1, keepdims=True)
    c_norm = np.linalg.norm(c, axis=1, keepdims=True)
    v_safe = np.divide(v, v_norm, out=np.zeros_like(v), where=v_norm > 1e-12)
    c_safe = np.divide(c, c_norm, out=np.zeros_like(c), where=c_norm > 1e-12)
    return v_safe @ c_safe.T


def _build_centroids_from_vectors(
    labeled: Sequence[TxFeatureRow],
    sim_categories: Sequence[str],
    vectors: np.ndarray,
) -> np.ndarray:
    dim = int(vectors.shape[1]) if vectors.ndim == 2 and vectors.shape[0] else 0
    if dim <= 0:
        return np.zeros((len(sim_categories), 1), dtype=np.float32)
    centroids = np.zeros((len(sim_categories), dim), dtype=np.float32)
    for i, cat in enumerate(sim_categories):
        idxs = [j for j, r in enumerate(labeled) if r.category == cat]
        if not idxs:
            continue
        centroids[i] = vectors[idxs].mean(axis=0)
    return centroids


def _build_centroids(
    labeled: Sequence[TxFeatureRow],
    sim_categories: Sequence[str],
    embedder: Embedder,
) -> np.ndarray:
    texts = [_embedding_text(r.payee, r.details) for r in labeled]
    vectors = embedder.embed_texts(texts)
    return _build_centroids_from_vectors(labeled, sim_categories, vectors)


def _feature_frame(
    rows: Sequence[TxFeatureRow],
    *,
    sim_categories: Sequence[str],
    centroids: Optional[np.ndarray],
    embedder: Optional[Embedder],
    precomputed_vectors: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    data: dict[str, Any] = {
        "payee": [r.payee for r in rows],
        "details": [r.details for r in rows],
        "outflow": [r.outflow for r in rows],
        "inflow": [r.inflow for r in rows],
    }
    if sim_categories and centroids is not None and len(rows) > 0:
        if precomputed_vectors is not None:
            vectors = precomputed_vectors
        elif embedder is not None:
            texts = [_embedding_text(r.payee, r.details) for r in rows]
            vectors = embedder.embed_texts(texts)
        else:
            vectors = None
        if vectors is not None:
            sims = _cosine_sims(vectors, centroids)
            for i, _cat in enumerate(sim_categories):
                data[f"sim_{i}"] = sims[:, i].astype(np.float32)
        else:
            for i in range(len(sim_categories)):
                data[f"sim_{i}"] = [0.0] * len(rows)
    elif sim_categories:
        for i in range(len(sim_categories)):
            data[f"sim_{i}"] = [0.0] * len(rows)
    return pd.DataFrame(data)


def _model_paths() -> tuple[str, str]:
    import config

    return config.category_catboost_model_file, config.category_catboost_meta_file


def _ensure_models_dir() -> None:
    import config

    os.makedirs(config.models_dir, exist_ok=True)


def load_model_meta(meta_path: Optional[str] = None) -> Optional[ModelMeta]:
    path = meta_path or _model_paths()[1]
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return ModelMeta.from_json(json.load(f))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        log.exception("failed to read category model meta %s", path)
        return None


def save_model_meta(meta: ModelMeta, meta_path: Optional[str] = None) -> None:
    _ensure_models_dir()
    path = meta_path or _model_paths()[1]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta.to_json(), f, ensure_ascii=False, indent=2)


class LocalMiniLMEmbedder:
    """fastembed MiniLM loaded from disk only (``local_files_only=True``)."""

    def __init__(self, cache_dir: str, *, model_name: str = MINILM_MODEL_NAME) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            local_files_only=True,
        )

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = list(self._model.embed(list(texts)))
        if not vectors:
            return np.zeros((0, 0), dtype=np.float32)
        return np.asarray(vectors, dtype=np.float32)


class StubEmbedder:
    """Deterministic vectors for unit tests (no MiniLM files)."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            lower = (text or "").lower()
            if "aws" in lower or "cloud" in lower:
                out[i, 0] = 1.0
            if "amzn" in lower or "amazon" in lower:
                out[i, 1] = 1.0
            if "station" in lower or "fuel" in lower:
                out[i, 2] = 1.0
            if "refund" in lower or "reimburse" in lower:
                out[i, 3] = 1.0
            # Small unique signal so identical keywords still differ slightly.
            h = abs(hash(lower)) % 997
            out[i, 4] = (h % 10) / 10.0
        return out


def try_load_minilm_embedder(cache_dir: Optional[str] = None) -> Optional[Embedder]:
    import config

    path = cache_dir or config.minilm_cache_dir
    if not os.path.isdir(path):
        log.warning("MiniLM cache missing at %s; training without embedding features", path)
        return None
    try:
        return LocalMiniLMEmbedder(path)
    except Exception:
        log.exception("failed to load MiniLM from %s; training without embedding features", path)
        return None


def fetch_minilm_model(*, cache_dir: Optional[str] = None) -> str:
    """Development-time download into ``data/models/minilm``. Not used at app startup."""
    from fastembed import TextEmbedding

    import config

    path = cache_dir or config.minilm_cache_dir
    os.makedirs(path, exist_ok=True)
    # Instantiating without local_files_only downloads into cache_dir.
    TextEmbedding(model_name=MINILM_MODEL_NAME, cache_dir=path)
    return path


def _train_catboost(
    frame: pd.DataFrame,
    labels: Sequence[str],
    *,
    iterations: int,
) -> Any:
    from catboost import CatBoostClassifier, Pool

    text_features = ["payee", "details"]
    pool = Pool(data=frame, label=list(labels), text_features=text_features)
    model = CatBoostClassifier(
        loss_function="MultiClass",
        iterations=iterations,
        depth=6,
        learning_rate=0.15,
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
        thread_count=-1,
        text_processing=_TEXT_PROCESSING,
    )
    model.fit(pool)
    return model


def train_category_model(
    labeled: Sequence[TxFeatureRow],
    *,
    corpus_key: str,
    embedder: Optional[Embedder] = None,
    model_path: Optional[str] = None,
    meta_path: Optional[str] = None,
    iterations: int = 80,
) -> Optional[tuple[Any, ModelMeta, Optional[np.ndarray]]]:
    """Train CatBoost on labeled rows. Returns ``None`` when training is not possible."""
    eligible = _eligible_categories(labeled)
    if len(eligible) < 2:
        log.info("category model: need at least 2 eligible classes; found %s", len(eligible))
        return None
    train_rows = [r for r in labeled if r.category in eligible]
    if len(train_rows) < 2:
        return None

    use_embedder = embedder
    sim_categories: tuple[str, ...] = tuple(eligible) if use_embedder is not None else ()
    centroids: Optional[np.ndarray] = None
    row_vectors: Optional[np.ndarray] = None
    if use_embedder is not None and sim_categories:
        try:
            texts = [_embedding_text(r.payee, r.details) for r in train_rows]
            log.info("category model: embedding %s labeled row(s) once", len(texts))
            row_vectors = use_embedder.embed_texts(texts)
            centroids = _build_centroids_from_vectors(train_rows, sim_categories, row_vectors)
        except Exception:
            log.exception("centroid build failed; continuing without embeddings")
            use_embedder = None
            sim_categories = ()
            centroids = None
            row_vectors = None

    frame = _feature_frame(
        train_rows,
        sim_categories=sim_categories,
        centroids=centroids,
        embedder=use_embedder,
        precomputed_vectors=row_vectors,
    )
    labels = [r.category for r in train_rows]
    log.info(
        "category model: fitting CatBoost iterations=%s rows=%s classes=%s",
        iterations,
        len(train_rows),
        len(eligible),
    )
    model = _train_catboost(frame, labels, iterations=iterations)

    cbm_path, default_meta = _model_paths()
    cbm_path = model_path or cbm_path
    meta_file = meta_path or default_meta
    _ensure_models_dir()
    model.save_model(cbm_path)

    meta = ModelMeta(
        corpus_key=corpus_key,
        sim_categories=sim_categories,
        has_embeddings=bool(sim_categories) and centroids is not None,
        model_version=corpus_key,
        top_n=_DEFAULT_TOP_N,
        min_confidence=_DEFAULT_MIN_CONFIDENCE,
    )
    save_model_meta(meta, meta_file)
    if centroids is not None and meta.has_embeddings:
        np.save(_centroids_path(cbm_path), centroids)
    return model, meta, centroids


def _centroids_path(model_path: str) -> str:
    return model_path + ".centroids.npy"


def load_trained_model(
    model_path: Optional[str] = None,
    meta_path: Optional[str] = None,
) -> Optional[tuple[Any, ModelMeta, Optional[np.ndarray]]]:
    from catboost import CatBoostClassifier

    cbm_path, default_meta = _model_paths()
    cbm_path = model_path or cbm_path
    meta_file = meta_path or default_meta
    meta = load_model_meta(meta_file)
    if meta is None or not os.path.isfile(cbm_path):
        return None
    model = CatBoostClassifier()
    model.load_model(cbm_path)
    centroids: Optional[np.ndarray] = None
    cent_path = _centroids_path(cbm_path)
    if meta.has_embeddings and os.path.isfile(cent_path):
        centroids = np.load(cent_path)
    return model, meta, centroids


def predict_suggestions(
    model: Any,
    meta: ModelMeta,
    rows: Sequence[TxFeatureRow],
    *,
    centroids: Optional[np.ndarray],
    embedder: Optional[Embedder],
    min_confidence: Optional[float] = None,
    top_n: Optional[int] = None,
) -> list[list[CategorySuggestion]]:
    """Return top-k class probabilities per row.

    ``min_confidence`` defaults to ``meta.min_confidence``. Pass ``0`` to always
    return a ranked list (evaluation / prediction explorer).
    """
    if not rows:
        return []
    floor = meta.min_confidence if min_confidence is None else float(min_confidence)
    k = meta.top_n if top_n is None else max(1, int(top_n))
    use_embedder = embedder if meta.has_embeddings else None
    frame = _feature_frame(
        rows,
        sim_categories=meta.sim_categories,
        centroids=centroids if meta.has_embeddings else None,
        embedder=use_embedder,
    )
    from catboost import Pool

    pool = Pool(data=frame, text_features=["payee", "details"])
    proba = model.predict_proba(pool)
    class_names = [str(c) for c in model.classes_]
    out: list[list[CategorySuggestion]] = []
    for row_probs in np.asarray(proba):
        ranked = sorted(
            zip(class_names, row_probs.tolist()),
            key=lambda x: x[1],
            reverse=True,
        )
        if not ranked or ranked[0][1] < floor:
            out.append([])
            continue
        top: list[CategorySuggestion] = []
        for category, prob in ranked[:k]:
            top.append(
                CategorySuggestion(
                    category=category,
                    confidence_pct=round(100.0 * float(prob), 1),
                )
            )
        out.append(top)
    return out


def suggestions_to_dicts(suggestions: Sequence[CategorySuggestion]) -> list[dict[str, Any]]:
    return [
        {"category": s.category, "confidence_pct": s.confidence_pct}
        for s in suggestions
    ]


def replace_suggestions_for_fingerprints(
    db_path: str,
    scored: Mapping[str, Sequence[CategorySuggestion]],
    *,
    model_version: str,
) -> None:
    """Replace stored suggestions for the given fingerprints (empty list deletes)."""
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect_ledger(db_path)
    try:
        for fp, suggestions in scored.items():
            fp = str(fp).strip()
            if not fp:
                continue
            if not suggestions:
                conn.execute("DELETE FROM category_suggestion WHERE fingerprint = ?", (fp,))
                continue
            payload = json.dumps(
                [{"category": s.category, "confidence_pct": s.confidence_pct} for s in suggestions],
                ensure_ascii=False,
            )
            conn.execute(
                """
                INSERT INTO category_suggestion (fingerprint, suggestions_json, model_version, scored_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    suggestions_json = excluded.suggestions_json,
                    model_version = excluded.model_version,
                    scored_at = excluded.scored_at
                """,
                (fp, payload, model_version, now),
            )
        # Drop suggestions for rows that are no longer uncategorized.
        conn.execute(
            """
            DELETE FROM category_suggestion
            WHERE fingerprint IN (
                SELECT fingerprint FROM ledger_transaction
                WHERE fingerprint IS NOT NULL
                  AND TRIM(COALESCE("קטגוריה", '')) NOT IN ('', 'awaiting')
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def load_stored_suggestions(
    db_path: str,
    fingerprint: str,
) -> list[CategorySuggestion]:
    fp = str(fingerprint or "").strip()
    if not fp:
        return []
    conn = ledger_connect_readonly(db_path)
    try:
        row = conn.execute(
            "SELECT suggestions_json FROM category_suggestion WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return []
    try:
        raw = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return []
    out: list[CategorySuggestion] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        cat = str(item.get("category") or "").strip()
        if not cat:
            continue
        try:
            pct = float(item.get("confidence_pct"))
        except (TypeError, ValueError):
            continue
        out.append(CategorySuggestion(category=cat, confidence_pct=pct))
    return out
