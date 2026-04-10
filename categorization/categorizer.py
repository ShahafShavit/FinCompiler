import asyncio
import difflib
import logging
import os
import re
import threading
import uuid
from typing import Mapping, Optional, Union

import pandas as pd
from bidi.algorithm import get_display
from numpy import nan

import config
from categorization.interactive.prompts import (
    FluidStorePrompt,
    NewStorePrompt,
    ResolveStaticPrompt,
)
from categorization.interactive.terminal import TerminalCategorizationHandler
from config import similar_categories_file
from pipeline.compiler import update_category_in_fingerprint_db

log = logging.getLogger(__name__)

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")

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


def _terminal_bidi(s):
    """Hebrew reads correctly in LTR-only terminals (logical → visual order)."""
    if s is None:
        return ""
    if isinstance(s, float) and pd.isna(s):
        return "nan"
    text = str(s)
    if _HEBREW_RE.search(text):
        return get_display(text)
    return text


def _terminal_bidi_seq(seq, left="{", right="}", *, sort=True):
    items = sorted(seq, key=lambda v: str(v)) if sort else list(seq)
    parts = []
    for x in items:
        if isinstance(x, float) and pd.isna(x):
            parts.append("nan")
        else:
            parts.append(repr(_terminal_bidi(str(x))))
    return left + ", ".join(parts) + right


class CategorizeFile:  # PRE COMPILER.. DATA FROM CLEAN DIR
    def __init__(self, file_path=None, *, ledger_db_path=None, interaction_handler=None):
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

            log.info("CategorizeFile: loading ledger %s", ledger_db_path)
            migrate_ledger_db(ledger_db_path)
            self.file_path = ledger_db_path
            self.file_name = os.path.basename(ledger_db_path)
            self.file_df = load_transactions_dataframe_from_ledger(ledger_db_path)
        else:
            log.info("CategorizeFile: loading %s", file_path)
            self.file_path = file_path
            self.file_name = os.path.basename(file_path)
            self.file_df = pd.read_csv(file_path)

        if "קטגוריה" not in self.file_df.columns:
            self.file_df["קטגוריה"] = pd.Series([""] * len(self.file_df), dtype=object, index=self.file_df.index)
        else:
            self.file_df["קטגוריה"] = (
                self.file_df["קטגוריה"].map(lambda x: "" if pd.isna(x) else str(x)).astype(object)
            )
        self.awaiting_df = pd.DataFrame(columns=self.file_df.columns)

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
            from pipeline.ledger import upsert_compiled_dataframe_to_ledger

            upsert_compiled_dataframe_to_ledger(self.file_df, self._ledger_db_path)
            return
        self.file_df.to_csv(self.file_path, index=False)

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
            mask = mask_rows_by_stable_id(self.file_df, str(transaction_id))
            if not mask.any():
                raise ValueError("transaction not in compiled file")
            self.file_df.loc[mask, "קטגוריה"] = new_category
            self.save_progress()
            if "fingerprint" in self.file_df.columns:
                fingerprint = self.file_df.loc[mask, "fingerprint"].iloc[0]
                if pd.notna(fingerprint):
                    update_category_in_fingerprint_db(fingerprint, new_category)
            self.category_store_link_backup(transaction_id, new_category)
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
        row_mask = mask_rows_by_stable_id(self.file_df, str(transaction_id))
        with self._io_lock:
            self.file_df.loc[row_mask, "קטגוריה"] = category
            self.save_progress()
            if "fingerprint" in self.file_df.columns:
                fingerprint = self.file_df.loc[row_mask, "fingerprint"].iloc[0]
                if pd.notna(fingerprint):
                    update_category_in_fingerprint_db(fingerprint, category)
        self.category_store_link_backup(transaction_id, category)

    def apply_manual_http_response(self, row_data, kind: str, data: dict) -> None:
        """Apply one queue answer (first unanswered row only); same side effects as interactive input."""
        self.load_stores()
        store_name = str(row_data["מקור עסקה"])
        transaction_id = stable_transaction_key(row_data)
        row_mask = mask_rows_by_stable_id(self.file_df, transaction_id)

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
            return

        raise ValueError("unknown kind")

    def apply_queue_revise(self, data: dict) -> Optional[str]:
        """Correction after an answer (same idea as HTTP revise); uses ``prompt_id`` = transaction id."""
        tid = str(data.get("prompt_id") or "")
        kind = data.get("kind")
        if not tid or not kind:
            return "prompt_id and kind required"
        self.load_stores()
        row_mask = mask_rows_by_stable_id(self.file_df, tid)
        if not row_mask.any():
            return "transaction not in compiled file"
        store_name = str(self.file_df.loc[row_mask, "מקור עסקה"].iloc[0])
        try:
            if kind == "fluid":
                new_cat = (data.get("category") or "").strip()
                if not new_cat:
                    return "category required"
                prev = str(self.file_df.loc[row_mask, "קטגוריה"].iloc[0])
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
                prev_cat = str(self.file_df.loc[row_mask, "קטגוריה"].iloc[0])
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
        """Fast count from current ``file_df`` (no auto pass)."""
        if self.file_df is None or self.file_df.empty:
            return 0
        col = self.file_df["קטגוריה"]
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
        log.info("auto_categorize: rows=%s file=%s", len(self.file_df), self.file_path)
        if not self._ledger_db_path:
            try:
                fp_db = pd.read_csv(config.fingerprint_db_file)
                if "category" in fp_db.columns and "fingerprint" in self.file_df.columns:
                    fp_db.dropna(subset=["category", "fingerprint"], inplace=True)
                    fp_db = fp_db[fp_db["category"] != ""]
                    category_map = pd.Series(fp_db.category.values, index=fp_db.fingerprint).to_dict()

                    uncategorized_mask = self.file_df["קטגוריה"].fillna("").eq("")
                    fingerprints_to_map = self.file_df.loc[uncategorized_mask, "fingerprint"]

                    new_categories = fingerprints_to_map.map(category_map)
                    self.file_df.loc[uncategorized_mask, "קטגוריה"] = self.file_df.loc[
                        uncategorized_mask, "קטגוריה"
                    ].fillna(new_categories)

                    log.info("Restored categories from fingerprint DB for uncategorized rows")
                    self.save_progress()
            except FileNotFoundError:
                log.warning("Fingerprint DB not found; skipping category restoration")
            except Exception as e:
                log.exception("Category restoration from fingerprint DB failed: %s", e)

        k_t = self.load_known_transactions()
        if k_t is None:
            k_t = pd.DataFrame(columns=['transaction_id', 'category'])
        self.load_stores()
        for index, row in self.file_df.iterrows():
            sid = stable_transaction_key(row)
            if len(k_t[k_t["transaction_id"] == sid]) == 1:
                category = k_t[k_t["transaction_id"] == sid]["category"].values[0]
                if category != row["קטגוריה"]:
                    self.file_df.loc[index, "קטגוריה"] = category
                    log.debug("Category from backup for id=%s -> %s", sid, category)
                    self.category_store_link_backup(sid, category)

            if row["קטגוריה"] == "" or row["קטגוריה"] == "awaiting" or pd.isna(row["קטגוריה"]):
                category = self.categorize_storename(row, method="auto")
                self.file_df.loc[index, "קטגוריה"] = category if category is not None else ""
                self.save_progress()
                if category is None:
                    self.awaiting_df.loc[len(self.awaiting_df)] = row
                if category is not None:
                    if "fingerprint" in row:
                        update_category_in_fingerprint_db(row["fingerprint"], category)
                    self.category_store_link_backup(sid, category)
            else:
                if "fingerprint" in row:
                    update_category_in_fingerprint_db(row["fingerprint"], row["קטגוריה"])
                self.category_store_link_backup(sid, row["קטגוריה"])
        awaiting = len(self.awaiting_df)
        if awaiting:
            log.info("auto_categorize: %s rows still awaiting manual category", awaiting)
        else:
            log.info("auto_categorize: complete (no awaiting rows)")

    def manual_categorizer(self, through='input', interaction_handler=None):
        log.info("manual_categorizer: engine=%s awaiting rows=%s", through, len(self.awaiting_df))
        if through.lower() not in ['input', 'discord']:
            raise ValueError("you must specify an engine manually (input, discord)")
        self.load_stores()
        h = interaction_handler or self.interaction
        try:
            for index, row in self.awaiting_df.iterrows():
                if row['קטגוריה'] == "" or row['קטגוריה'] == "awaiting" or pd.isna(row['קטגוריה']):
                    category = self.categorize_storename(row, method='input', interaction_handler=h)

                    row_mask = mask_rows_by_stable_id(self.file_df, stable_transaction_key(row))
                    with self._io_lock:
                        self.file_df.loc[row_mask, 'קטגוריה'] = category
                        self.save_progress()
                        if 'fingerprint' in self.file_df.columns:
                            fingerprint = self.file_df.loc[row_mask, 'fingerprint'].iloc[0]
                            if pd.notna(fingerprint):
                                update_category_in_fingerprint_db(fingerprint, category)

                self.awaiting_df.drop(index=index, inplace=True)
        finally:
            closer = getattr(h, "close", None)
            if callable(closer):
                closer()
        log.info("manual_categorizer: loop complete, awaiting_df rows=%s", len(self.awaiting_df))

    @staticmethod
    def fix_null_category_status():
        fix_amount = 10
        while fix_amount > 0:
            if os.path.isfile(config.ledger_db_file):
                from pipeline.ledger import load_stores_dataframe_from_ledger

                stores_df = load_stores_dataframe_from_ledger(config.ledger_db_file)
            else:
                stores_df = pd.read_csv(config.stores_to_categories_file)
            fix_amount = len(stores_df[stores_df['is_static'] == -1])
            for index, row in stores_df.iterrows():
                category = row['category']
                store_name = row['store_name']
                if row['is_static'] not in [1, 0]:
                    log.info(
                        "fix_null_category_status: store=%s category=%s",
                        _terminal_bidi(row["store_name"]),
                        _terminal_bidi(row["category"]),
                    )
                    is_static_input = input(
                        f"Is this category: [{_terminal_bidi(category)}] for {_terminal_bidi(store_name)} static?"
                        f"\n (Type '0' if dynamic, type '1' if static): ")
                    is_static = int(is_static_input) if int(is_static_input) in [1, 0] else -1
                    stores_df.loc[stores_df["store_name"] == store_name, "is_static"] = is_static
                    if os.path.isfile(config.ledger_db_file):
                        from pipeline.ledger import sync_stores_to_ledger_from_dataframe

                        sync_stores_to_ledger_from_dataframe(config.ledger_db_file, stores_df)
                    else:
                        stores_df.to_csv(config.stores_to_categories_file, index=False)
                    fix_amount -= 1
                    break

    @staticmethod
    def fix_nan_category():
        if os.path.isfile(config.ledger_db_file):
            from pipeline.ledger import load_stores_dataframe_from_ledger

            stores_df = load_stores_dataframe_from_ledger(config.ledger_db_file)
        else:
            stores_df = pd.read_csv(config.stores_to_categories_file)
        categories_to_check = set(stores_df["category"].tolist())
        stores_df["category"].fillna("NULL")
        if os.path.isfile(config.ledger_db_file):
            from pipeline.ledger import sync_stores_to_ledger_from_dataframe

            sync_stores_to_ledger_from_dataframe(config.ledger_db_file, stores_df)
        else:
            stores_df.to_csv(config.stores_to_categories_file, index=False)

    @staticmethod
    def fix_similar_categories_in_file():
        log.info("fix_similar_categories_in_file: starting")
        if os.path.isfile(config.ledger_db_file):
            from pipeline.ledger import (
                load_known_transactions_backup_from_ledger,
                load_stores_dataframe_from_ledger,
                load_transactions_dataframe_from_ledger,
            )

            stores_df = load_stores_dataframe_from_ledger(config.ledger_db_file)
            compiled_df = load_transactions_dataframe_from_ledger(config.ledger_db_file)
            backup_df = (
                load_known_transactions_backup_from_ledger(config.ledger_db_file)
                or pd.DataFrame(columns=["transaction_id", "category"])
            )
            import sqlite3

            conn = sqlite3.connect(config.ledger_db_file)
            try:
                linked_pairs = pd.read_sql_query(
                    "SELECT p1, p2 FROM similar_category_pair", conn
                )
            finally:
                conn.close()
        else:
            stores_df = pd.read_csv(config.stores_to_categories_file)
            compiled_df = pd.read_csv(config.compiled_file)
            backup_df = pd.read_csv(config.transaction_category_file)
            linked_pairs = pd.read_csv(similar_categories_file)
        stores_df["category"] = stores_df["category"].replace(nan, "NULL")
        categories_to_check = set(stores_df["category"].tolist())
        not_to_check = []

        for category in categories_to_check:
            if category not in not_to_check:
                ans = difflib.get_close_matches(category, categories_to_check, n=3)
                if len(ans) > 1:
                    match_ratio = difflib.SequenceMatcher(None, ans[0], ans[1]).ratio()
                    if match_ratio > 0.7:
                        pair = [ans[0], ans[1]]
                        if pair in linked_pairs.values.tolist() or list(reversed(pair)) in linked_pairs.values.tolist():
                            continue
                        log.info("Similar categories check for: %s", _terminal_bidi(category))
                        for i, option in enumerate(ans, 1):
                            log.info("  %s. %s", i, _terminal_bidi(option))
                        ans_input = input(f"Choose:\n1. Keep first\n2. Keep second\n3. Keep both\n")
                        if ans_input in ['1', '2']:
                            choice = int(ans_input) - 1
                            category = ans[choice]
                            stores_df.loc[((stores_df['category'] == ans[0]) | (
                                    stores_df['category'] == ans[1])), 'category'] = category
                            backup_df.loc[((backup_df['category'] == ans[0]) | (
                                    backup_df['category'] == ans[1])), 'category'] = category
                            compiled_df.loc[(compiled_df['קטגוריה'] == ans[0]) | (
                                    compiled_df['קטגוריה'] == ans[1]), 'קטגוריה'] = category
                        not_to_check.extend(ans)
                        if ans_input == "3":
                            linked_pairs.loc[len(linked_pairs)] = pair
        if os.path.isfile(config.ledger_db_file):
            import sqlite3

            from pipeline.ledger import upsert_compiled_dataframe_to_ledger
            from pipeline.ledger import sync_stores_to_ledger_from_dataframe

            sync_stores_to_ledger_from_dataframe(config.ledger_db_file, stores_df)
            upsert_compiled_dataframe_to_ledger(compiled_df, config.ledger_db_file)
            conn = sqlite3.connect(config.ledger_db_file)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("DELETE FROM similar_category_pair")
                if not linked_pairs.empty:
                    conn.executemany(
                        "INSERT INTO similar_category_pair (p1, p2) VALUES (?, ?)",
                        [(str(r["p1"]), str(r["p2"])) for _, r in linked_pairs.iterrows()],
                    )
                conn.commit()
            finally:
                conn.close()
        else:
            linked_pairs.to_csv(config.similar_categories_file, index=False)
            stores_df.to_csv(config.stores_to_categories_file, index=False)
            compiled_df.to_csv(config.compiled_file, index=False)
            backup_df.to_csv(config.transaction_category_file, index=False)

    @staticmethod
    def rename_category(old_name, new_name):
        pass

    @staticmethod
    def category_store_link_backup(transaction_id, category):
        if os.path.isfile(config.ledger_db_file):
            return
        if not os.path.isfile(config.transaction_category_file):
            data = {"transaction_id": [transaction_id], "category": [category]}
            df = pd.DataFrame(data=data)
            df.to_csv(config.transaction_category_file, index=False)
        else:
            data = {"transaction_id": transaction_id, "category": category}
            df = pd.read_csv(config.transaction_category_file)
            exists = df.loc[df["transaction_id"] == transaction_id]
            if exists.empty:
                df.loc[len(df)] = data
            else:
                df.loc[df["transaction_id"] == transaction_id, "category"] = category
            df.drop_duplicates(subset=["transaction_id"], inplace=True, keep="last")
            df.to_csv(config.transaction_category_file, index=False)

    @staticmethod
    def update_store_category(store_name, category):
        if os.path.isfile(config.ledger_db_file):
            from pipeline.ledger import load_stores_dataframe_from_ledger

            df = load_stores_dataframe_from_ledger(config.ledger_db_file)
        else:
            df = pd.read_csv(config.stores_to_categories_file)
        match = df[df["store_name"] == store_name]
        if len(match) >= 2:
            df.loc[df['store_name'] == store_name, 'is_static'] = 0
            if len(match.loc[match['category'] == category]) == 0:
                df.loc[len(df)] = [store_name, category, 0]
            else:
                df.drop_duplicates(subset=['store_name', 'category'], inplace=True)
        elif len(match) == 1:
            if match['category'].item() != category:
                if match['is_static'].item() == 1:
                    log.warning(
                        "Store %s: new category %s vs existing static %s",
                        _terminal_bidi(store_name),
                        _terminal_bidi(category),
                        _terminal_bidi(match["category"].item()),
                    )
                    ans = input(
                        "Type: \n1 to modify current static category for store. \n2 to change store to dynamic and add "
                        "category.\n3 to ignore.\n")
                    if ans == '1':
                        df.loc[df['store_name'] == store_name, 'category'] = category
                    elif ans == '2':
                        df.loc[df['store_name'] == store_name, 'is_static'] = 0
                        df.loc[len(df)] = [store_name, category, 0]
                    else:
                        log.info("User skipped category update (option 3 or invalid)")
                        return
                if match['is_static'].item() == 0:
                    df.loc[len(df)] = [store_name, category, 0]
        else:
            log.error("update_store_category: store not in stores file (no match)")
            return
        if os.path.isfile(config.ledger_db_file):
            from pipeline.ledger import sync_stores_to_ledger_from_dataframe

            sync_stores_to_ledger_from_dataframe(config.ledger_db_file, df)
            return
        df.to_csv(config.stores_to_categories_file, index=False)

    @staticmethod
    def dupe_seeker():
        if os.path.isfile(config.ledger_db_file):
            from pipeline.ledger import load_transactions_dataframe_from_ledger

            log.info("dupe_seeker: scanning ledger %s", config.ledger_db_file)
            df = load_transactions_dataframe_from_ledger(config.ledger_db_file)
        else:
            log.info("dupe_seeker: scanning %s", config.compiled_file)
            df = pd.read_csv(config.compiled_file)

        grouped_expenses = df[df['בחובה'] != 0].groupby(['תאריך', 'בחובה']).size().reset_index(name='counts')
        filtered_groups = grouped_expenses[grouped_expenses['counts'] > 1]
        result_expenses = pd.merge(df, filtered_groups[['תאריך', 'בחובה']], on=['תאריך', 'בחובה'], how='inner')

        grouped_incomes = df[df['בזכות'] != 0].groupby(['תאריך', 'בזכות']).size().reset_index(name='counts')
        filtered_groups = grouped_incomes[grouped_incomes['counts'] > 1]
        result_incomes = pd.merge(df, filtered_groups[['תאריך', 'בזכות']], on=['תאריך', 'בזכות'], how='inner')

        log.info("Duplicate expense rows (by date+amount): %s", len(result_expenses))
        log.debug("Expense dupes:\n%s", result_expenses)
        log.info("Duplicate income rows (by date+amount): %s", len(result_incomes))
        log.debug("Income dupes:\n%s", result_incomes)


if __name__ == "__main__":
    # main_df = pd.read_csv(config.compiled_file)
    # kt_df = pd.read_csv(config.transaction_category_file)
    # missing_transactions = main_df[~main_df['מזהה עסקה'].isin(kt_df['transaction_id'])]
    # print(missing_transactions)
    f = CategorizeFile(ledger_db_path=config.ledger_db_file)
    # f.fix_categories()
    # f.auto_categorize()
    # f.manual_categorizer()
