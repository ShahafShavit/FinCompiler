"""
Load static CSVs into the ledger DB (MIG-D2):

- ``stores_to_categories.csv`` → ``store`` + ``store_category``
- ``similar_pairs.csv`` → ``similar_category_pair``

``is_static`` in the stores CSV is per row; the DB model stores **one** ``is_static`` per ``store_name``.

- If a store has **more than one distinct category**, the store is **dynamic** (``is_static = 0``).
- If a store has **exactly one** category row, ``store.is_static`` follows that row's flag (0/1).
- Duplicate ``(store_name, category)`` rows: last row wins before aggregation.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

import pandas as pd

import config
from pipeline.ledger_migrate import migrate_ledger_db

log = logging.getLogger(__name__)

_STORE_COLS = ["store_name", "category", "is_static"]
_SIMILAR_COLS = ["p1", "p2"]


def _coerce_is_static(val: Any) -> int:
    if pd.isna(val):
        return 0
    try:
        i = int(round(float(val)))
    except (TypeError, ValueError):
        return 0
    return 1 if i == 1 else 0


def load_stores_to_categories_dataframe(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    missing = [c for c in _STORE_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}; expected {_STORE_COLS}")
    df = df.copy()
    # Drop null store/category before str coercion — NaN must not become the literal "nan"
    # (groupby excludes NA keys; iterrows would still emit them and break store_category FKs).
    df = df.dropna(subset=["store_name", "category"])
    df["store_name"] = df["store_name"].astype(str).str.strip()
    df["category"] = df["category"].astype(str).str.strip()
    df = df[(df["store_name"] != "") & (df["category"] != "")]
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["store_name", "category"], keep="last")
    return df.reset_index(drop=True)


def load_similar_pairs_dataframe(csv_path: str) -> pd.DataFrame:
    """Return ``p1``/``p2`` rows; empty frame if file is missing (caller may warn)."""
    if not os.path.isfile(csv_path):
        return pd.DataFrame(columns=_SIMILAR_COLS)
    df = pd.read_csv(csv_path)
    missing = [c for c in _SIMILAR_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}; expected {_SIMILAR_COLS}")
    df = df.copy()
    df = df.dropna(subset=["p1", "p2"])
    df["p1"] = df["p1"].astype(str).str.strip()
    df["p2"] = df["p2"].astype(str).str.strip()
    df = df[(df["p1"] != "") & (df["p2"] != "")]
    df = df[df["p1"] != df["p2"]]
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["p1", "p2"], keep="last")
    return df.reset_index(drop=True)


def import_stores_to_ledger(
    csv_path: str | None = None,
    db_path: str | None = None,
    *,
    similar_pairs_csv: str | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """
    Load store/category mappings and similar category pairs into the ledger DB.

    Runs ``migrate_ledger_db`` first. If ``replace`` is True, deletes all ``store`` rows
    (``store_category`` cascades) and all ``similar_category_pair`` rows before insert.

    ``similar_pairs_csv`` defaults to ``config.similar_categories_file``. If that file is
    missing, similar pairs are skipped (table cleared when ``replace``).
    """
    path = csv_path if csv_path is not None else config.stores_to_categories_file
    sim_path = similar_pairs_csv if similar_pairs_csv is not None else config.similar_categories_file
    db = db_path if db_path is not None else config.ledger_db_file
    migrate_ledger_db(db)

    warnings: list[str] = []
    if not os.path.isfile(sim_path):
        warnings.append(f"Similar pairs file not found (skipped): {sim_path}")

    sim_df = load_similar_pairs_dataframe(sim_path)
    df = load_stores_to_categories_dataframe(path)

    if df.empty and sim_df.empty:
        conn = sqlite3.connect(db)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            if replace:
                conn.execute("DELETE FROM store")
                conn.execute("DELETE FROM similar_category_pair")
                conn.commit()
        finally:
            conn.close()
        msg = "No store/category rows after filter"
        if not os.path.isfile(sim_path):
            msg += "; similar pairs file missing"
        elif sim_df.empty and os.path.isfile(sim_path):
            msg += "; similar_pairs had no valid rows"
        return {
            "ok": True,
            "csv_path": path,
            "similar_pairs_csv": sim_path,
            "db_path": db,
            "stores_inserted": 0,
            "store_category_rows_inserted": 0,
            "similar_pair_rows_inserted": 0,
            "stores_forced_dynamic": 0,
            "warnings": [msg],
        }

    store_rows: list[tuple[str, int]] = []
    forced_dynamic = 0
    if not df.empty:
        for store_name, sub in df.groupby("store_name", sort=True):
            cats = sub["category"].unique()
            if len(cats) > 1:
                static = 0
                if (sub["is_static"].map(_coerce_is_static) == 1).any():
                    forced_dynamic += 1
            else:
                static = _coerce_is_static(sub["is_static"].iloc[0])
            store_rows.append((str(store_name), static))

    sc_tuples = (
        [(str(r["store_name"]), str(r["category"])) for _, r in df.iterrows()] if not df.empty else []
    )
    sim_tuples = [(str(r["p1"]), str(r["p2"])) for _, r in sim_df.iterrows()]

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if replace:
            conn.execute("DELETE FROM store")
            conn.execute("DELETE FROM similar_category_pair")
        if store_rows:
            conn.executemany(
                "INSERT INTO store (store_name, is_static) VALUES (?, ?)",
                store_rows,
            )
        if sc_tuples:
            conn.executemany(
                "INSERT INTO store_category (store_name, category) VALUES (?, ?)",
                sc_tuples,
            )
        if sim_tuples:
            conn.executemany(
                "INSERT INTO similar_category_pair (p1, p2) VALUES (?, ?)",
                sim_tuples,
            )
        conn.commit()
        n_store = conn.execute("SELECT COUNT(*) FROM store").fetchone()[0]
        n_sc = conn.execute("SELECT COUNT(*) FROM store_category").fetchone()[0]
        n_sim = conn.execute("SELECT COUNT(*) FROM similar_category_pair").fetchone()[0]
    finally:
        conn.close()

    if forced_dynamic:
        warnings.append(
            f"{forced_dynamic} store(s) had is_static=1 on some row but multiple categories; "
            "set to dynamic (is_static=0)."
        )

    log.info(
        "static import: %s stores, %s store_category, %s similar pairs → %s",
        n_store,
        n_sc,
        n_sim,
        db,
    )
    return {
        "ok": True,
        "csv_path": path,
        "similar_pairs_csv": sim_path,
        "db_path": db,
        "stores_inserted": int(n_store),
        "store_category_rows_inserted": int(n_sc),
        "similar_pair_rows_inserted": int(n_sim),
        "stores_forced_dynamic": forced_dynamic,
        "warnings": warnings,
    }


def sync_stores_to_ledger_from_dataframe(db_path: str, df: pd.DataFrame) -> None:
    """
    Replace ``store`` / ``store_category`` from an in-memory frame (same columns as the CSV).

    Does **not** modify ``similar_category_pair`` (pairs stay in the DB until a full static import).
    """
    migrate_ledger_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM store")
        if df.empty:
            conn.commit()
            return

        missing = [c for c in _STORE_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"stores dataframe missing columns: {missing}")

        work = df.copy()
        work = work.dropna(subset=["store_name", "category"])
        work["store_name"] = work["store_name"].astype(str).str.strip()
        work["category"] = work["category"].astype(str).str.strip()
        work = work[(work["store_name"] != "") & (work["category"] != "")]
        if work.empty:
            conn.commit()
            return
        work = work.drop_duplicates(subset=["store_name", "category"], keep="last")

        store_rows: list[tuple[str, int]] = []
        forced_dynamic = 0
        for store_name, sub in work.groupby("store_name", sort=True):
            cats = sub["category"].unique()
            if len(cats) > 1:
                static = 0
                if (sub["is_static"].map(_coerce_is_static) == 1).any():
                    forced_dynamic += 1
            else:
                static = _coerce_is_static(sub["is_static"].iloc[0])
            store_rows.append((str(store_name), static))

        sc_tuples = [(str(r["store_name"]), str(r["category"])) for _, r in work.iterrows()]

        if store_rows:
            conn.executemany(
                "INSERT INTO store (store_name, is_static) VALUES (?, ?)",
                store_rows,
            )
        if sc_tuples:
            conn.executemany(
                "INSERT INTO store_category (store_name, category) VALUES (?, ?)",
                sc_tuples,
            )
        conn.commit()
        if forced_dynamic:
            log.warning(
                "%s store(s) forced dynamic (is_static=1 with multiple categories)",
                forced_dynamic,
            )
    finally:
        conn.close()
