"""Web-driven transaction categorization and ledger/category persistence.

The control server exposes ``/api/*`` queue endpoints (:mod:`api.categorize_queue`). The
transactions pipeline imports :class:`CategorizeFile` for a post-compile **auto** pass only
(:meth:`auto_categorize`); manual answers always go through the HTTP queue.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union

import pandas as pd

import config
from pipeline.compiler import update_category_in_fingerprint_db

log = logging.getLogger(__name__)


def _scalar_for_json(x: Any) -> Any:
    """Normalize CSV/pandas cell values so :func:`json.dumps` is browser-safe (no numpy types, no NaN)."""
    if x is None:
        return None
    try:
        import pandas as pd

        if isinstance(x, pd.Timestamp):
            if x.hour == 0 and x.minute == 0 and x.second == 0 and x.microsecond == 0:
                return x.strftime("%Y-%m-%d")
            return x.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    try:
        from datetime import date, datetime

        if isinstance(x, datetime):
            if x.hour == 0 and x.minute == 0 and x.second == 0 and x.microsecond == 0:
                return x.date().isoformat()
            return x.isoformat(timespec="seconds")
        if isinstance(x, date):
            return x.isoformat()
    except Exception:
        pass
    try:
        import numpy as np

        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, np.floating):
            v = float(x)
            if v != v or v in (float("inf"), float("-inf")):  # nan / inf
                return None
            return v
        if isinstance(x, np.bool_):
            return bool(x)
    except Exception:
        pass
    try:
        import pandas as pd

        if isinstance(x, float) and pd.isna(x):
            return None
    except Exception:
        pass
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    try:
        import pandas as pd

        if pd.isna(x) and not isinstance(x, (str, bytes)):
            return None
    except Exception:
        pass
    try:
        import json

        json.dumps(x)
        return x
    except (TypeError, ValueError):
        return str(x)


@dataclass(frozen=True)
class FluidStorePrompt:
    """Existing store with fluid (non-static) categories — pick or type a category."""

    store_name: str
    date: Any
    expense: Any
    income: Any
    details: Optional[Any]
    digits: Optional[Any]
    dynamic_categories: tuple[str, ...]
    all_categories: tuple[str, ...]
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "fluid",
            "prompt_id": self.prompt_id,
            "transaction_id": _scalar_for_json(self.transaction_id),
            "store_name": _scalar_for_json(self.store_name),
            "date": _scalar_for_json(self.date),
            "expense": _scalar_for_json(self.expense),
            "income": _scalar_for_json(self.income),
            "details": _scalar_for_json(self.details),
            "digits": _scalar_for_json(self.digits),
            "dynamic_categories": list(self.dynamic_categories),
            "all_categories": list(self.all_categories),
        }


@dataclass(frozen=True)
class ResolveStaticPrompt:
    """Ambiguous is_static flag on a store row — user picks 0 (fluid) or 1 (static)."""

    store_name: str
    category: str
    date: Any
    expense: Any
    income: Any
    details: Optional[Any]
    digits: Optional[Any]
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "resolve_static",
            "prompt_id": self.prompt_id,
            "transaction_id": _scalar_for_json(self.transaction_id),
            "store_name": _scalar_for_json(self.store_name),
            "category": _scalar_for_json(self.category),
            "date": _scalar_for_json(self.date),
            "expense": _scalar_for_json(self.expense),
            "income": _scalar_for_json(self.income),
            "details": _scalar_for_json(self.details),
            "digits": _scalar_for_json(self.digits),
        }


@dataclass(frozen=True)
class NewStorePrompt:
    """Store not in list — choose category and whether mapping is static or fluid."""

    store_name: str
    date: Any
    expense: Any
    income: Any
    details: Optional[Any]
    digits: Optional[Any]
    all_categories: tuple[str, ...]
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "new_store",
            "prompt_id": self.prompt_id,
            "transaction_id": _scalar_for_json(self.transaction_id),
            "store_name": _scalar_for_json(self.store_name),
            "date": _scalar_for_json(self.date),
            "expense": _scalar_for_json(self.expense),
            "income": _scalar_for_json(self.income),
            "details": _scalar_for_json(self.details),
            "digits": _scalar_for_json(self.digits),
            "all_categories": list(self.all_categories),
        }


ManualPrompt = Union[FluidStorePrompt, ResolveStaticPrompt, NewStorePrompt]


def stable_transaction_key(row: Union[pd.Series, Mapping]) -> str:
    """Stable id for prompts and HTTP queue. Prefer **fingerprint** (ledger dedupe key).

    Fall back to **מזהה עסקה** only when that legacy CSV hash column exists — it is **not** a SQLite column.
    """
    if isinstance(row, pd.Series):
        fp_ok = "fingerprint" in row.index
        leg_ok = "מזהה עסקה" in row.index
        fp = row["fingerprint"] if fp_ok else None
        leg = row["מזהה עסקה"] if leg_ok else None
    else:
        fp = row.get("fingerprint")
        leg = row.get("מזהה עסקה")
    if fp is not None and pd.notna(fp) and str(fp).strip():
        return str(fp).strip()
    if leg is not None and pd.notna(leg):
        return str(leg)
    return ""


def mask_rows_by_stable_id(df: pd.DataFrame, key: str) -> pd.Series:
    """Match ``key`` to ``fingerprint``, then ``מזהה עסקה``."""
    k = str(key)
    if "fingerprint" in df.columns:
        m = df["fingerprint"].astype(str) == k
        if m.any():
            return m
    if "מזהה עסקה" in df.columns:
        return df["מזהה עסקה"].astype(str) == k
    return pd.Series([False] * len(df), index=df.index)


def category_cell_needs_manual(cat) -> bool:
    """True if compiled row still needs a user-chosen category."""
    if cat is None:
        return True
    try:
        if isinstance(cat, float) and pd.isna(cat):
            return True
    except Exception:
        pass
    try:
        if pd.isna(cat) and not isinstance(cat, str):
            return True
    except Exception:
        pass
    s = str(cat).strip()
    return s == "" or s.lower() == "awaiting"


def _normalized_category_value(cat) -> str:
    """Comparable form for whether a קטגוריה cell changed."""
    if cat is None:
        return ""
    try:
        if isinstance(cat, float) and pd.isna(cat):
            return ""
    except Exception:
        pass
    try:
        if pd.isna(cat) and not isinstance(cat, str):
            return ""
    except Exception:
        pass
    return str(cat).strip()


class CategorizeFile:
    """Categorization: CSV staging (legacy) or SQLite ledger.

    Web flow: query uncategorized rows, one ``UPDATE`` per saved answer; when a store becomes
    static (``is_static = 1``), forward-fill runs for **other** uncategorized rows for that store
    only (see :func:`pipeline.ledger.forward_fill_uncategorized_for_store_if_static_sql`).
    """

    def __init__(
        self,
        file_path=None,
        *,
        ledger_db_path=None,
        materialize_transactions: bool = True,
    ):
        if ledger_db_path and file_path:
            raise ValueError("pass either file_path or ledger_db_path, not both")
        if not ledger_db_path and not file_path:
            raise ValueError("compiled CSV path or ledger_db_path is required")
        self.stores_df = None
        self._ledger_db_path = ledger_db_path
        self._io_lock = threading.Lock()

        if ledger_db_path:
            from pipeline.ledger import load_transactions_dataframe_from_ledger
            from pipeline.ledger import migrate_ledger_db

            migrate_ledger_db(ledger_db_path)
            self.file_path = ledger_db_path
            self.file_name = os.path.basename(ledger_db_path)
            if materialize_transactions:
                log.info("CategorizeFile: loading ledger %s", ledger_db_path)
                self.transactions_df = load_transactions_dataframe_from_ledger(ledger_db_path)
            else:
                log.debug(
                    "CategorizeFile: ledger %s (stores/prompts only; transaction table not loaded)",
                    ledger_db_path,
                )
                self.transactions_df = pd.DataFrame({"קטגוריה": pd.Series([], dtype=object)})
            self._materialize_transactions = materialize_transactions
        else:
            if os.path.isfile(config.ledger_db_file) and os.path.normcase(
                os.path.abspath(file_path)
            ) == os.path.normcase(os.path.abspath(config.compiled_file)):
                log.warning(
                    "CategorizeFile loading compiled CSV %s while ledger exists at %s — "
                    "use ledger_db_path=%s for native SQLite categorization",
                    file_path,
                    config.ledger_db_file,
                    repr(config.ledger_db_file),
                )
            log.info("CategorizeFile: loading %s", file_path)
            self.file_path = file_path
            self.file_name = os.path.basename(file_path)
            self.transactions_df = pd.read_csv(file_path)
            self._materialize_transactions = True

        if "קטגוריה" not in self.transactions_df.columns:
            self.transactions_df["קטגוריה"] = pd.Series(
                [""] * len(self.transactions_df), dtype=object, index=self.transactions_df.index
            )
        else:
            self.transactions_df["קטגוריה"] = (
                self.transactions_df["קטגוריה"].map(lambda x: "" if pd.isna(x) else str(x)).astype(object)
            )
        self.awaiting_df = pd.DataFrame(columns=self.transactions_df.columns)
        self._ledger_category_dirty: dict[str, str] = {}

    def _queue_ledger_category(self, fingerprint: object, category: object) -> None:
        """Batch category writes for :meth:`save_progress` (SQLite ``UPDATE`` by fingerprint, no full upsert)."""
        if not self._ledger_db_path:
            return
        if fingerprint is None or (isinstance(fingerprint, float) and pd.isna(fingerprint)):
            return
        fp = str(fingerprint).strip()
        if not fp:
            return
        self._ledger_category_dirty[fp] = "" if category is None else str(category)

    def _use_legacy_fingerprint_csv_sidecar(self) -> bool:
        """True only when no ledger on disk — legacy ``fingerprint_db.csv`` (MIG-E3)."""
        if self._ledger_db_path:
            return False
        if os.path.isfile(config.ledger_db_file):
            return False
        return True

    def load_stores(self):
        if self._ledger_db_path:
            from pipeline.ledger import load_stores_dataframe_from_ledger

            self.stores_df = load_stores_dataframe_from_ledger(self._ledger_db_path)
            return

        path = config.stores_to_categories_file
        if not os.path.isfile(path):
            log.warning(
                "Store list not found at %s; creating empty stores_to_categories.csv",
                path,
            )
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self.stores_df = pd.DataFrame(
                columns=["store_name", "category", "is_static"],
            )
            self.save_stores()
            return
        self.stores_df = pd.read_csv(path)

    def save_stores(self):
        if self._ledger_db_path:
            from pipeline.ledger import sync_stores_to_ledger_from_dataframe

            sync_stores_to_ledger_from_dataframe(self._ledger_db_path, self.stores_df)
            return
        self.stores_df.to_csv(config.stores_to_categories_file, index=False)

    def save_progress(self):
        if self._ledger_db_path:
            from pipeline.ledger import update_categories_by_fingerprint_batch

            if self._ledger_category_dirty:
                n = update_categories_by_fingerprint_batch(
                    self._ledger_db_path,
                    list(self._ledger_category_dirty.items()),
                )
                self._ledger_category_dirty.clear()
                if n:
                    log.info(
                        "ledger categories: batch-updated %s row(s) in %s",
                        n,
                        self._ledger_db_path,
                    )
            return
        self.transactions_df.to_csv(self.file_path, index=False)

    def apply_session_category_revision(
        self,
        transaction_id: str,
        store_name: str,
        new_category: str,
        *,
        previous_category: Optional[str] = None,
    ) -> None:
        """Update compiled row + fingerprint/backup; best-effort rename in stores_to_categories."""
        new_category = (new_category or "").strip()
        if not new_category:
            raise ValueError("category required")
        with self._io_lock:
            self.load_stores()
            if self._ledger_db_path:
                from pipeline.ledger import load_ledger_transaction_by_stable_id
                from pipeline.ledger import update_category_by_fingerprint

                row = load_ledger_transaction_by_stable_id(self._ledger_db_path, str(transaction_id))
                if row is None:
                    raise ValueError("transaction not in compiled file")
                fp = str(row.get("fingerprint", "")).strip()
                if not fp:
                    raise ValueError("transaction has no fingerprint")
                update_category_by_fingerprint(self._ledger_db_path, fp, new_category)
                prev = (previous_category or "").strip()
                if prev and prev != new_category:
                    sm = self.stores_df["store_name"] == store_name
                    cm = self.stores_df["category"] == prev
                    if (sm & cm).any():
                        self.stores_df.loc[sm & cm, "category"] = new_category
                        self.save_stores()
                return

            mask = mask_rows_by_stable_id(self.transactions_df, str(transaction_id))
            if not mask.any():
                raise ValueError("transaction not in compiled file")
            self.transactions_df.loc[mask, "קטגוריה"] = new_category
            if "fingerprint" in self.transactions_df.columns:
                fingerprint = self.transactions_df.loc[mask, "fingerprint"].iloc[0]
                if pd.notna(fingerprint):
                    self._queue_ledger_category(fingerprint, new_category)
                    if not self._ledger_db_path:
                        update_category_in_fingerprint_db(fingerprint, new_category)
            self.save_progress()
            prev = (previous_category or "").strip()
            if prev and prev != new_category:
                sm = self.stores_df["store_name"] == store_name
                cm = self.stores_df["category"] == prev
                if (sm & cm).any():
                    self.stores_df.loc[sm & cm, "category"] = new_category
                    self.save_stores()

    def apply_session_resolve_static_revision(self, store_name: str, category: str, is_static: int) -> None:
        if is_static not in (0, 1):
            raise ValueError("is_static must be 0 or 1")
        with self._io_lock:
            self.load_stores()
            m = (self.stores_df["store_name"] == store_name) & (self.stores_df["category"] == category)
            if not m.any():
                raise ValueError("store/category not in stores list")
            self.stores_df.loc[m, "is_static"] = int(is_static)
            self.save_stores()
            self._ledger_forward_fill_uncategorized_if_static(store_name, is_static)

    def apply_session_new_store_static_revision(self, store_name: str, category: str, is_static: int) -> None:
        """Set is_static for an existing (store_name, category) row after the user revises new_store."""
        if is_static not in (0, 1):
            raise ValueError("is_static must be 0 or 1")
        with self._io_lock:
            self.load_stores()
            m = (self.stores_df["store_name"] == store_name) & (self.stores_df["category"] == category)
            if not m.any():
                raise ValueError("store mapping not found")
            self.stores_df.loc[m, "is_static"] = int(is_static)
            self.save_stores()
            self._ledger_forward_fill_uncategorized_if_static(store_name, is_static)

    def categorize_storename(self, row_data, method: str = "auto"):
        """Return category from static store mapping only (``method`` must be ``\"auto\"``)."""
        if method != "auto":
            raise ValueError('only method="auto" is supported; use the web queue for manual categorization')
        store_name: str = row_data["מקור עסקה"]
        self.load_stores()
        if self.stores_df is None or self.stores_df.empty:
            return None
        match = self.stores_df[
            (self.stores_df["store_name"] == store_name) & (self.stores_df["is_static"] == 1)
        ]
        if match.empty:
            return None
        return match["category"].iloc[0]

    def build_manual_prompt_for_row(self, row_data) -> ManualPrompt:
        """Build the next question for ``row_data`` (``prompt_id`` = stable transaction id for the queue API)."""
        self.load_stores()
        store_name = str(row_data["מקור עסקה"])
        transaction_id = stable_transaction_key(row_data)
        date = row_data["תאריך"]
        expense = row_data["בחובה"]
        income = row_data["בזכות"]
        details = row_data["תאור מורחב"] if "תאור מורחב" in row_data.keys() else None
        digits = row_data["4 ספרות"] if "4 ספרות" in row_data.keys() else None
        pid = transaction_id
        if self.stores_df is None or self.stores_df.empty:
            all_categories: tuple[str, ...] = tuple()
        else:
            all_categories = tuple(sorted(set(self.stores_df["category"].tolist()), key=str))

        def _static_flag(s) -> int:
            if s is None or (isinstance(s, float) and pd.isna(s)):
                return -999
            try:
                return int(float(s))
            except (TypeError, ValueError):
                return -999

        for _, srow in self.stores_df.iterrows():
            recorded_store = srow["store_name"]
            category = srow["category"]
            iv = _static_flag(srow["is_static"])
            if store_name != recorded_store:
                continue
            if iv == 1:
                raise ValueError(
                    f"queue inconsistency: store {store_name!r} is static but row lacks category"
                )
            if iv == 0:
                dynamic_categories = tuple(
                    self.stores_df[self.stores_df["store_name"] == store_name]["category"].tolist()
                )
                return FluidStorePrompt(
                    store_name=store_name,
                    date=date,
                    expense=expense,
                    income=income,
                    details=details,
                    digits=digits,
                    dynamic_categories=dynamic_categories,
                    all_categories=all_categories,
                    transaction_id=transaction_id,
                    prompt_id=pid,
                )
            prompt = ResolveStaticPrompt(
                store_name=store_name,
                category=category,
                date=date,
                expense=expense,
                income=income,
                details=details,
                digits=digits,
                transaction_id=transaction_id,
                prompt_id=pid,
            )
            return prompt
        return NewStorePrompt(
            store_name=store_name,
            date=date,
            expense=expense,
            income=income,
            details=details,
            digits=digits,
            all_categories=all_categories,
            transaction_id=transaction_id,
            prompt_id=pid,
        )

    def _persist_category_for_transaction(self, transaction_id: str, category: str) -> None:
        if self._ledger_db_path:
            from pipeline.ledger import load_ledger_transaction_by_stable_id
            from pipeline.ledger import update_category_by_fingerprint

            row = load_ledger_transaction_by_stable_id(self._ledger_db_path, str(transaction_id))
            if row is None:
                raise ValueError("transaction not in ledger")
            fp = str(row.get("fingerprint", "")).strip()
            if not fp:
                raise ValueError("transaction has no fingerprint")
            with self._io_lock:
                update_category_by_fingerprint(self._ledger_db_path, fp, category)
            return

        row_mask = mask_rows_by_stable_id(self.transactions_df, str(transaction_id))
        with self._io_lock:
            self.transactions_df.loc[row_mask, "קטגוריה"] = category
            if "fingerprint" in self.transactions_df.columns:
                fingerprint = self.transactions_df.loc[row_mask, "fingerprint"].iloc[0]
                if pd.notna(fingerprint):
                    self._queue_ledger_category(fingerprint, category)
                    if not self._ledger_db_path:
                        update_category_in_fingerprint_db(fingerprint, category)
            self.save_progress()

    def apply_manual_http_response(self, row_data, kind: str, data: dict) -> None:
        """Apply one queue answer (first unanswered row only)."""
        self.load_stores()
        store_name = str(row_data["מקור עסקה"])
        transaction_id = stable_transaction_key(row_data)

        if kind == "fluid":
            category_input = (data.get("category") or "").strip()
            if not category_input:
                raise ValueError("category required")
            dynamic_categories = self.stores_df[self.stores_df["store_name"] == store_name][
                "category"
            ].tolist()
            if category_input not in dynamic_categories:
                sub = self.stores_df[self.stores_df["store_name"] == store_name]
                is_static_val = 0
                for _, r in sub.iterrows():
                    try:
                        if int(float(r["is_static"])) == 0:
                            is_static_val = 0
                            break
                    except (TypeError, ValueError):
                        continue
                new_row = {
                    "store_name": store_name,
                    "category": category_input,
                    "is_static": is_static_val,
                }
                self.stores_df.loc[len(self.stores_df)] = new_row
                self.save_stores()
            self._persist_category_for_transaction(transaction_id, category_input)
            return

        if kind == "resolve_static":
            v = data.get("is_static")
            if v not in (0, 1):
                raise ValueError("is_static must be 0 or 1")
            ambig = self.stores_df[
                (self.stores_df["store_name"] == store_name)
                & (~self.stores_df["is_static"].isin([0, 1]))
            ]
            if ambig.empty:
                raise ValueError("no ambiguous static row for store")
            category = ambig.iloc[0]["category"]
            self.stores_df.loc[self.stores_df["store_name"] == store_name, "is_static"] = int(v)
            self.save_stores()
            self._persist_category_for_transaction(transaction_id, str(category))
            self._ledger_forward_fill_uncategorized_if_static(store_name, int(v))
            return

        if kind == "new_store":
            category_input = (data.get("category") or "").strip()
            v = data.get("is_static")
            if not category_input:
                raise ValueError("category required")
            if v not in (0, 1):
                raise ValueError("is_static must be 0 or 1")
            new_row = {
                "store_name": store_name,
                "category": category_input,
                "is_static": int(v),
            }
            self.stores_df.loc[len(self.stores_df)] = new_row
            self.save_stores()
            self._persist_category_for_transaction(transaction_id, category_input)
            self._ledger_forward_fill_uncategorized_if_static(store_name, int(v))
            return

        raise ValueError("unknown kind")

    def apply_queue_revise(self, data: dict) -> Optional[str]:
        """Correction after an answer (same idea as HTTP revise); uses ``prompt_id`` = transaction id."""
        tid = str(data.get("prompt_id") or "")
        kind = data.get("kind")
        if not tid or not kind:
            return "prompt_id and kind required"
        self.load_stores()
        ldb_row = None
        row_mask = None
        if self._ledger_db_path:
            from pipeline.ledger import load_ledger_transaction_by_stable_id

            ldb_row = load_ledger_transaction_by_stable_id(self._ledger_db_path, tid)
            if ldb_row is None:
                return "transaction not in compiled file"
            store_name = str(ldb_row["מקור עסקה"])
        else:
            row_mask = mask_rows_by_stable_id(self.transactions_df, tid)
            if not row_mask.any():
                return "transaction not in compiled file"
            store_name = str(self.transactions_df.loc[row_mask, "מקור עסקה"].iloc[0])
        try:
            if kind == "fluid":
                new_cat = (data.get("category") or "").strip()
                if not new_cat:
                    return "category required"
                if self._ledger_db_path:
                    prev = str(ldb_row.get("קטגוריה", "") or "")
                else:
                    prev = str(self.transactions_df.loc[row_mask, "קטגוריה"].iloc[0])
                self.apply_session_category_revision(
                    tid, store_name, new_cat, previous_category=prev
                )
            elif kind == "new_store":
                new_cat = (data.get("category") or "").strip()
                v = data.get("is_static")
                if not new_cat:
                    return "category required"
                if v not in (0, 1):
                    return "is_static must be 0 or 1"
                if self._ledger_db_path:
                    prev_cat = str(ldb_row.get("קטגוריה", "") or "")
                else:
                    prev_cat = str(self.transactions_df.loc[row_mask, "קטגוריה"].iloc[0])
                if str(prev_cat or "").strip() != new_cat:
                    self.apply_session_category_revision(
                        tid, store_name, new_cat, previous_category=prev_cat
                    )
                self.apply_session_new_store_static_revision(store_name, new_cat, int(v))
            elif kind == "resolve_static":
                v = data.get("is_static")
                if v not in (0, 1):
                    return "is_static must be 0 or 1"
                ambig = self.stores_df[
                    (self.stores_df["store_name"] == store_name)
                    & (~self.stores_df["is_static"].isin([0, 1]))
                ]
                if ambig.empty:
                    return "store/category not ambiguous"
                cat = str(ambig.iloc[0]["category"])
                self.apply_session_resolve_static_revision(store_name, cat, int(v))
            else:
                return "unknown kind"
        except ValueError as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("queue revise failed: %s", e)
            return str(e)
        return None

    def count_rows_needing_category(self) -> int:
        """Rows still needing a manual category (ledger: live query; CSV: ``transactions_df``)."""
        if self._ledger_db_path:
            from pipeline.ledger import count_transactions_needing_manual_category

            return count_transactions_needing_manual_category(self._ledger_db_path)
        if self.transactions_df is None or self.transactions_df.empty:
            return 0
        col = self.transactions_df["קטגוריה"]
        return int(col.map(lambda c: category_cell_needs_manual(c)).sum())

    def load_known_transactions(self):
        """Backup category hints keyed by stable id (fingerprint from ledger; CSV legacy uses ``transaction_id``)."""
        if self._ledger_db_path:
            from pipeline.ledger import load_known_transactions_backup_from_ledger

            return load_known_transactions_backup_from_ledger(self._ledger_db_path)
        if os.path.isfile(config.transaction_category_file):
            df = pd.read_csv(config.transaction_category_file)
            df.drop_duplicates(subset=["transaction_id"], inplace=True, keep="first")
            return df
        return None

    def auto_categorize(self):
        if self._ledger_db_path:
            from pipeline.ledger import count_ledger_transaction_rows
            from pipeline.ledger import count_transactions_needing_manual_category
            from pipeline.ledger import forward_fill_uncategorized_for_static_stores_sql
            from pipeline.ledger import load_transactions_dataframe_from_ledger

            total_rows = (
                len(self.transactions_df)
                if self._materialize_transactions
                else count_ledger_transaction_rows(self._ledger_db_path)
            )
            log.info("auto_categorize: rows=%s file=%s", total_rows, self.file_path)
            self.awaiting_df = pd.DataFrame(columns=self.transactions_df.columns)
            n = forward_fill_uncategorized_for_static_stores_sql(self._ledger_db_path)
            if n:
                log.info("auto_categorize (ledger): forward_fill static stores → %s row(s)", n)
            if self._materialize_transactions:
                self.transactions_df = load_transactions_dataframe_from_ledger(self._ledger_db_path)
            awaiting = count_transactions_needing_manual_category(self._ledger_db_path)
            if awaiting:
                log.info("auto_categorize: %s rows still awaiting manual category", awaiting)
            else:
                log.info("auto_categorize: complete (no awaiting rows)")
            return

        log.info("auto_categorize: rows=%s file=%s", len(self.transactions_df), self.file_path)
        self.awaiting_df = pd.DataFrame(columns=self.transactions_df.columns)

        progress_dirty = False
        if self._use_legacy_fingerprint_csv_sidecar():
            try:
                fp_db = pd.read_csv(config.fingerprint_db_file)
                if "category" in fp_db.columns and "fingerprint" in self.transactions_df.columns:
                    fp_db.dropna(subset=["category", "fingerprint"], inplace=True)
                    fp_db = fp_db[fp_db["category"] != ""]
                    category_map = pd.Series(fp_db.category.values, index=fp_db.fingerprint).to_dict()

                    uncategorized_mask = self.transactions_df["קטגוריה"].fillna("").eq("")
                    fingerprints_to_map = self.transactions_df.loc[uncategorized_mask, "fingerprint"]

                    new_categories = fingerprints_to_map.map(category_map)
                    self.transactions_df.loc[uncategorized_mask, "קטגוריה"] = self.transactions_df.loc[
                        uncategorized_mask, "קטגוריה"
                    ].fillna(new_categories)

                    log.info("Restored categories from fingerprint DB for uncategorized rows")
                    progress_dirty = True
            except FileNotFoundError:
                log.warning("Fingerprint DB not found; skipping category restoration")
            except Exception as e:
                log.exception("Category restoration from fingerprint DB failed: %s", e)

        k_t = self.load_known_transactions()
        if k_t is None:
            k_t = pd.DataFrame(columns=["transaction_id", "category"])
        self.load_stores()
        for index, row in self.transactions_df.iterrows():
            sid = stable_transaction_key(row)
            if len(k_t[k_t["transaction_id"] == sid]) == 1:
                category = k_t[k_t["transaction_id"] == sid]["category"].values[0]
                if _normalized_category_value(category) != _normalized_category_value(
                    self.transactions_df.loc[index, "קטגוריה"]
                ):
                    self.transactions_df.loc[index, "קטגוריה"] = category
                    fp = row["fingerprint"] if "fingerprint" in row.index else None
                    self._queue_ledger_category(fp, category)
                    progress_dirty = True
                    log.debug("Category from backup for id=%s -> %s", sid, category)

            cur = self.transactions_df.loc[index, "קטגוריה"]
            if category_cell_needs_manual(cur):
                category = self.categorize_storename(self.transactions_df.loc[index], method="auto")
                new_val = category if category is not None else ""
                if _normalized_category_value(new_val) != _normalized_category_value(cur):
                    self.transactions_df.loc[index, "קטגוריה"] = new_val
                    fp = row["fingerprint"] if "fingerprint" in row.index else None
                    self._queue_ledger_category(fp, new_val)
                    progress_dirty = True
                if category is not None:
                    if not self._ledger_db_path and "fingerprint" in row.index:
                        update_category_in_fingerprint_db(row["fingerprint"], category)
                else:
                    if not self._ledger_db_path:
                        self.awaiting_df.loc[len(self.awaiting_df)] = self.transactions_df.loc[index]
            else:
                if not self._ledger_db_path and "fingerprint" in row.index:
                    update_category_in_fingerprint_db(row["fingerprint"], row["קטגוריה"])
        if progress_dirty or self._ledger_category_dirty:
            self.save_progress()
        awaiting = len(self.awaiting_df)
        if awaiting:
            log.info("auto_categorize: %s rows still awaiting manual category", awaiting)
        else:
            log.info("auto_categorize: complete (no awaiting rows)")

    def _ledger_forward_fill_uncategorized_if_static(self, store_name: str, is_static: int) -> None:
        """Bulk-fill other uncategorized rows for this store when it is static (never overwrites set categories)."""
        if not self._ledger_db_path or int(is_static) != 1:
            return
        from pipeline.ledger import forward_fill_uncategorized_for_store_if_static_sql

        n = forward_fill_uncategorized_for_store_if_static_sql(
            self._ledger_db_path,
            str(store_name).strip(),
        )
        if n:
            log.info(
                "ledger forward_fill: %s uncategorized row(s) for store %r (static)",
                n,
                store_name,
            )
