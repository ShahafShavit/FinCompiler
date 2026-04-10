import logging
import os
import threading
import uuid
from typing import Mapping, Optional, Union

import pandas as pd

import config
from categorization.interactive.prompts import (
    FluidStorePrompt,
    NewStorePrompt,
    ResolveStaticPrompt,
)
from categorization.interactive.terminal import TerminalCategorizationHandler
from categorization import maintenance as _maintenance

category_store_link_backup = _maintenance.category_store_link_backup
from pipeline.compiler import update_category_in_fingerprint_db

log = logging.getLogger(__name__)

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
        interaction_handler=None,
        materialize_transactions: bool = True,
    ):
        if ledger_db_path and file_path:
            raise ValueError("pass either file_path or ledger_db_path, not both")
        if not ledger_db_path and not file_path:
            raise ValueError("compiled CSV path or ledger_db_path is required")
        self.stores_df = None
        self._ledger_db_path = ledger_db_path
        self.interaction = interaction_handler or TerminalCategorizationHandler()
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
            self.transactions_df["קטגוריה"] = pd.Series([""] * len(self.transactions_df), dtype=object, index=self.transactions_df.index)
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
                category_store_link_backup(transaction_id, new_category)
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
            category_store_link_backup(transaction_id, new_category)
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

    def categorize_storename(self, row_data, method='auto', interaction_handler=None):
        store_name: str = row_data['מקור עסקה']
        transaction_id = stable_transaction_key(row_data)
        date = row_data['תאריך']
        expense = row_data['בחובה']
        income = row_data['בזכות']
        details = None
        digits = None
        if 'תאור מורחב' in row_data.keys():
            details = row_data['תאור מורחב']
        if '4 ספרות' in row_data.keys():
            digits = row_data['4 ספרות']

        if method == 'auto':
            if self.stores_df is None or self.stores_df.empty:
                return None
            # Match any static row for this store (must scan full table; a previous bug returned
            # after the first row only).
            match = self.stores_df[
                (self.stores_df["store_name"] == store_name) & (self.stores_df["is_static"] == 1)
            ]
            if match.empty:
                return None
            return match["category"].iloc[0]
        elif method == 'input':
            h = interaction_handler or self.interaction
            all_categories = set(self.stores_df['category'].tolist())
            for _, row in self.stores_df.iterrows():
                recorded_store, category, is_static = row['store_name'], row['category'], row['is_static']
                if store_name == recorded_store:
                    if is_static == 1:
                        return category
                    if is_static == 0:
                        dynamic_categories = self.stores_df[self.stores_df['store_name'] == store_name][
                            'category'].tolist()
                        prompt = FluidStorePrompt(
                            store_name=store_name,
                            date=date,
                            expense=expense,
                            income=income,
                            details=details,
                            digits=digits,
                            dynamic_categories=tuple(dynamic_categories),
                            all_categories=tuple(sorted(all_categories, key=str)),
                            transaction_id=transaction_id,
                            prompt_id=uuid.uuid4().hex,
                        )
                        category_input = h.prompt_fluid_store(prompt).strip()
                        if category_input in dynamic_categories:
                            return category_input
                        log.info("New category added to store list for existing fluid store")
                        new_row = {'store_name': store_name, 'category': category_input, 'is_static': is_static}
                        self.stores_df.loc[len(self.stores_df)] = new_row
                        self.save_stores()
                        return category_input
                    # is_static is ambiguous (-1 or other)
                    prompt = ResolveStaticPrompt(
                        store_name=store_name,
                        category=category,
                        date=date,
                        expense=expense,
                        income=income,
                        details=details,
                        digits=digits,
                        transaction_id=transaction_id,
                        prompt_id=uuid.uuid4().hex,
                    )
                    is_static_new = h.prompt_resolve_static(prompt)
                    self.stores_df.loc[self.stores_df['store_name'] == store_name, 'is_static'] = is_static_new
                    self.save_stores()
                    return category
            prompt = NewStorePrompt(
                store_name=store_name,
                date=date,
                expense=expense,
                income=income,
                details=details,
                digits=digits,
                all_categories=tuple(sorted(all_categories, key=str)),
                transaction_id=transaction_id,
                prompt_id=uuid.uuid4().hex,
            )
            category_input, is_static_input = h.prompt_new_store(prompt)
            new_row = {'store_name': store_name, 'category': category_input, 'is_static': int(is_static_input)}
            self.stores_df.loc[len(self.stores_df)] = new_row
            self.save_stores()
            return category_input

    def build_manual_prompt_for_row(self, row_data) -> ManualPrompt:
        """
        Same branching as ``categorize_storename(..., method='input')`` up to the first prompt,
        but returns the prompt object instead of blocking. ``prompt_id`` is the transaction id
        so the queue API stays stable across polls.
        """
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
            category_store_link_backup(transaction_id, category)
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
        category_store_link_backup(transaction_id, category)

    def apply_manual_http_response(self, row_data, kind: str, data: dict) -> None:
        """Apply one queue answer (first unanswered row only); same side effects as interactive input."""
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
            k_t = pd.DataFrame(columns=['transaction_id', 'category'])
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
                    category_store_link_backup(sid, category)

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
                    category_store_link_backup(sid, category)
                else:
                    if not self._ledger_db_path:
                        self.awaiting_df.loc[len(self.awaiting_df)] = self.transactions_df.loc[index]
            else:
                if not self._ledger_db_path and "fingerprint" in row.index:
                    update_category_in_fingerprint_db(row["fingerprint"], row["קטגוריה"])
                category_store_link_backup(sid, row["קטגוריה"])
        if progress_dirty or self._ledger_category_dirty:
            self.save_progress()
        awaiting = len(self.awaiting_df)
        if awaiting:
            log.info("auto_categorize: %s rows still awaiting manual category", awaiting)
        else:
            log.info("auto_categorize: complete (no awaiting rows)")

    def manual_categorizer(self, through="input", interaction_handler=None):
        if through.lower() != "input":
            raise ValueError("engine must be 'input'")
        if self._ledger_db_path:
            self._manual_categorizer_ledger(through, interaction_handler)
            return

        log.info("manual_categorizer: engine=%s awaiting rows=%s", through, len(self.awaiting_df))
        self.load_stores()
        h = interaction_handler or self.interaction
        try:
            for index, row in self.awaiting_df.iterrows():
                if row['קטגוריה'] == "" or row['קטגוריה'] == "awaiting" or pd.isna(row['קטגוריה']):
                    category = self.categorize_storename(row, method='input', interaction_handler=h)

                    row_mask = mask_rows_by_stable_id(self.transactions_df, stable_transaction_key(row))
                    with self._io_lock:
                        self.transactions_df.loc[row_mask, 'קטגוריה'] = category
                        if 'fingerprint' in self.transactions_df.columns:
                            fingerprint = self.transactions_df.loc[row_mask, 'fingerprint'].iloc[0]
                            if pd.notna(fingerprint):
                                self._queue_ledger_category(fingerprint, category)
                                if not self._ledger_db_path:
                                    update_category_in_fingerprint_db(fingerprint, category)
                        self.save_progress()

                self.awaiting_df.drop(index=index, inplace=True)
        finally:
            closer = getattr(h, "close", None)
            if callable(closer):
                closer()
        log.info("manual_categorizer: loop complete, awaiting_df rows=%s", len(self.awaiting_df))

    def _manual_categorizer_ledger(self, through: str, interaction_handler) -> None:
        """SQLite: pull next uncategorized row, prompt, ``UPDATE`` — no dataframe staging."""
        from pipeline.ledger import count_transactions_needing_manual_category
        from pipeline.ledger import load_first_transaction_needing_manual_category
        from pipeline.ledger import load_transactions_dataframe_from_ledger
        from pipeline.ledger import update_category_by_fingerprint

        self.load_stores()
        h = interaction_handler or self.interaction
        n_open = count_transactions_needing_manual_category(self._ledger_db_path)
        log.info("manual_categorizer (ledger): engine=%s rows_needing_category=%s", through, n_open)
        try:
            while True:
                row = load_first_transaction_needing_manual_category(self._ledger_db_path)
                if row is None:
                    break
                category = self.categorize_storename(row, method="input", interaction_handler=h)
                fp = row.get("fingerprint")
                if fp is None or (isinstance(fp, float) and pd.isna(fp)) or not str(fp).strip():
                    log.warning("manual_categorizer: skip row without fingerprint")
                    continue
                update_category_by_fingerprint(self._ledger_db_path, str(fp).strip(), category)
        finally:
            closer = getattr(h, "close", None)
            if callable(closer):
                closer()
        self.transactions_df = load_transactions_dataframe_from_ledger(self._ledger_db_path)
        log.info(
            "manual_categorizer (ledger): complete; remaining=%s",
            count_transactions_needing_manual_category(self._ledger_db_path),
        )

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

CategorizeFile.fix_null_category_status = staticmethod(_maintenance.fix_null_category_status)
CategorizeFile.fix_nan_category = staticmethod(_maintenance.fix_nan_category)
CategorizeFile.fix_similar_categories_in_file = staticmethod(_maintenance.fix_similar_categories_in_file)
CategorizeFile.rename_category = staticmethod(_maintenance.rename_category)
CategorizeFile.category_store_link_backup = staticmethod(_maintenance.category_store_link_backup)
CategorizeFile.update_store_category = staticmethod(_maintenance.update_store_category)
CategorizeFile.dupe_seeker = staticmethod(_maintenance.dupe_seeker)
