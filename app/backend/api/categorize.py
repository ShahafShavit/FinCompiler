"""Web-driven transaction categorization and ledger/category persistence.

The control server exposes ``/api/*`` queue endpoints (:mod:`api.categorize_queue`). The
transactions pipeline imports :class:`CategorizeFile` for a post-compile **auto** pass only
(:meth:`auto_categorize`); manual answers always go through the HTTP queue.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Union

import pandas as pd

import config

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


def _nonzero_amount(x: Any) -> bool:
    """True if the normalized cell represents a non-zero monetary amount."""
    v = _scalar_for_json(x)
    if v is None:
        return False
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return abs(float(v)) > 1e-12
    try:
        return abs(float(str(v).strip().replace(",", ""))) > 1e-12
    except (TypeError, ValueError):
        return False


def flow_kind_for_amounts(expense: Any, income: Any) -> str:
    """UI/API classifier: expense | income | both | none (never debit/credit)."""
    e = _nonzero_amount(expense)
    i = _nonzero_amount(income)
    if e and i:
        return "both"
    if e:
        return "expense"
    if i:
        return "income"
    return "none"


def _optional_row_scalar(row_data: Union[pd.Series, Mapping], key: str) -> Any:
    if isinstance(row_data, pd.Series):
        return row_data[key] if key in row_data.index else None
    return row_data.get(key) if isinstance(row_data, Mapping) else None


def _ledger_display_context(row_data: Union[pd.Series, Mapping]) -> dict[str, Any]:
    """Extra columns from ledger_transaction for the categorize UI (English JSON keys)."""
    return {
        "ledger_id": _optional_row_scalar(row_data, "id"),
        "additional_detail": _optional_row_scalar(row_data, "פירוט נוסף"),
        "notes": _optional_row_scalar(row_data, "notes"),
        "statement_month": _optional_row_scalar(row_data, "statement_month"),
        "row_fingerprint": _optional_row_scalar(row_data, "fingerprint"),
        "ingested_at": _optional_row_scalar(row_data, "ingested_at"),
    }


def _merge_ledger_into_display(d: dict[str, Any], prompt: Any) -> dict[str, Any]:
    """Append normalized ledger context to a prompt display dict (no fingerprint — stable id is ``transaction_id``)."""
    d = {**d}
    d["ledger_id"] = _scalar_for_json(getattr(prompt, "ledger_id", None))
    d["additional_detail"] = _scalar_for_json(getattr(prompt, "additional_detail", None))
    d["notes"] = _scalar_for_json(getattr(prompt, "notes", None))
    d["statement_month"] = _scalar_for_json(getattr(prompt, "statement_month", None))
    d["ingested_at"] = _scalar_for_json(getattr(prompt, "ingested_at", None))
    return d


def _parse_store_is_static_cell(iv_raw: Any) -> int | None:
    """0 / 1 for valid flags, ``None`` if missing or not exactly 0/1."""
    try:
        if iv_raw is None or (isinstance(iv_raw, float) and pd.isna(iv_raw)):
            return None
        v = int(float(iv_raw))
        return v if v in (0, 1) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PayeeMappingEnvelope:
    """Serialized store-table rows for the payee shown in the categorize UI."""

    rows: tuple[tuple[str, int | None], ...]
    kind: str
    summary: str

    def to_json_dict(self) -> dict[str, Any]:
        distinct = len({c for c, _ in self.rows if c})
        return {
            "payee_store_mappings": [{"category": c, "is_static": iv} for c, iv in self.rows],
            "payee_mapping_kind": self.kind,
            "payee_mapping_summary": self.summary,
            "payee_distinct_category_count": distinct,
        }


def _default_payee_mapping_unmapped() -> PayeeMappingEnvelope:
    return PayeeMappingEnvelope(
        tuple(),
        "unmapped",
        "This payee has no rows in the store mapping table yet.",
    )


def _payee_mapping_envelope_from_stores(stores_df: Optional[pd.DataFrame], store_name: str) -> PayeeMappingEnvelope:
    """Summarize ``store`` / ``store_category`` rows for this payee (``store_name`` column)."""
    if stores_df is None or stores_df.empty:
        return _default_payee_mapping_unmapped()
    sub = stores_df[stores_df["store_name"] == store_name]
    if sub.empty:
        return _default_payee_mapping_unmapped()

    raw_rows: list[tuple[str, int | None]] = []
    for _, r in sub.iterrows():
        cat = r["category"]
        if cat is None or (isinstance(cat, float) and pd.isna(cat)):
            cat_s = ""
        else:
            cat_s = str(cat).strip()
        raw_rows.append((cat_s, _parse_store_is_static_cell(r["is_static"])))

    raw_rows.sort(key=lambda t: (t[0].lower(), t[1] is None, t[1] if t[1] is not None else -1))
    rows_t = tuple(raw_rows)

    flags = [iv for _, iv in raw_rows]
    has_bad_flag = any(iv is None for iv in flags)
    any_static = any(iv == 1 for iv in flags)
    any_dynamic = any(iv == 0 for iv in flags)
    distinct_cats = len({c for c, _ in raw_rows if c})

    if has_bad_flag:
        kind = "ambiguous"
        summary = (
            "At least one store row for this payee has an unclear static/dynamic flag. "
            "Use the buttons below to set whether this mapping is static or dynamic for future imports."
        )
    elif any_static and any_dynamic:
        kind = "mixed"
        summary = (
            "This payee has both static and dynamic rows in the store table (unusual). "
            "See the list below—review for duplicates or inconsistent data."
        )
    elif any_static:
        kind = "static"
        if distinct_cats <= 1:
            summary = (
                "This payee is mapped as static: the same category is applied automatically "
                "to new ledger rows for this name (when the pipeline runs)."
            )
        else:
            summary = (
                "This payee has several static-marked categories (unusual). See the list below; "
                "normally only one static category should exist per payee."
            )
    elif any_dynamic:
        kind = "dynamic"
        summary = (
            f"This payee is mapped as dynamic: {distinct_cats} distinct "
            f"categor{'y is' if distinct_cats == 1 else 'ies are'} already linked; "
            "you may reuse one or add another for this row."
        )
    else:
        kind = "unmapped"
        summary = "No usable static/dynamic flags on store rows for this payee."

    return PayeeMappingEnvelope(rows_t, kind, summary)


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
    ledger_id: Any = None
    additional_detail: Any = None
    notes: Any = None
    statement_month: Any = None
    row_fingerprint: Any = None
    ingested_at: Any = None
    payee_mapping: PayeeMappingEnvelope = field(default_factory=_default_payee_mapping_unmapped)
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        d = _merge_ledger_into_display(
            {
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
            },
            self,
        )
        return {**d, **self.payee_mapping.to_json_dict()}


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
    ledger_id: Any = None
    additional_detail: Any = None
    notes: Any = None
    statement_month: Any = None
    row_fingerprint: Any = None
    ingested_at: Any = None
    payee_mapping: PayeeMappingEnvelope = field(default_factory=_default_payee_mapping_unmapped)
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        d = _merge_ledger_into_display(
            {
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
            },
            self,
        )
        return {**d, **self.payee_mapping.to_json_dict()}


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
    ledger_id: Any = None
    additional_detail: Any = None
    notes: Any = None
    statement_month: Any = None
    row_fingerprint: Any = None
    ingested_at: Any = None
    payee_mapping: PayeeMappingEnvelope = field(default_factory=_default_payee_mapping_unmapped)
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        d = _merge_ledger_into_display(
            {
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
            },
            self,
        )
        return {**d, **self.payee_mapping.to_json_dict()}


ManualPrompt = Union[FluidStorePrompt, ResolveStaticPrompt, NewStorePrompt]


def stable_transaction_key(row: Union[pd.Series, Mapping]) -> str:
    """Stable id for prompts and HTTP queue: ledger ``fingerprint`` only.

    Must match :func:`ledger.store.load_ledger_transaction_by_stable_id` lookup (trimmed fingerprint).
    """
    if isinstance(row, pd.Series):
        fp = row["fingerprint"] if "fingerprint" in row.index else None
    else:
        fp = row.get("fingerprint")
    if fp is not None and pd.notna(fp) and str(fp).strip():
        return str(fp).strip()
    return ""


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
    """Categorization against the SQLite ledger only.

    Web flow: query uncategorized rows, one ``UPDATE`` per saved answer; when a store becomes
    static (``is_static = 1``), forward-fill runs for **other** uncategorized rows for that store
    only (see :func:`ledger.forward_fill_uncategorized_for_store_if_static_sql`).
    """

    def __init__(
        self,
        *,
        ledger_db_path: str | None = None,
        materialize_transactions: bool = True,
    ):
        db = ledger_db_path or config.ledger_db_file
        if not db or not os.path.isfile(db):
            raise ValueError(
                "CategorizeFile requires an existing ledger database "
                f"(ledger_db_path={ledger_db_path!r}, resolved={db!r})"
            )
        self.stores_df = None
        self._ledger_db_path = db
        self._io_lock = threading.Lock()

        from ledger import load_transactions_dataframe_from_ledger
        from ledger import migrate_ledger_db

        migrate_ledger_db(db)
        self.file_path = db
        self.file_name = os.path.basename(db)
        if materialize_transactions:
            log.info("CategorizeFile: loading ledger %s", db)
            self.transactions_df = load_transactions_dataframe_from_ledger(db)
        else:
            log.debug(
                "CategorizeFile: ledger %s (stores/prompts only; transaction table not loaded)",
                db,
            )
            self.transactions_df = pd.DataFrame({"קטגוריה": pd.Series([], dtype=object)})
        self._materialize_transactions = materialize_transactions

        if "קטגוריה" not in self.transactions_df.columns:
            self.transactions_df["קטגוריה"] = pd.Series(
                [""] * len(self.transactions_df), dtype=object, index=self.transactions_df.index
            )
        else:
            self.transactions_df["קטגוריה"] = (
                self.transactions_df["קטגוריה"].map(lambda x: "" if pd.isna(x) else str(x)).astype(object)
            )
        self.awaiting_df = pd.DataFrame(columns=self.transactions_df.columns)

    def load_stores(self):
        from ledger import load_stores_dataframe_from_ledger

        self.stores_df = load_stores_dataframe_from_ledger(self._ledger_db_path)

    def save_stores(self):
        from ledger import sync_stores_to_ledger_from_dataframe

        sync_stores_to_ledger_from_dataframe(self._ledger_db_path, self.stores_df)

    def apply_session_category_revision(
        self,
        transaction_id: str,
        store_name: str,
        new_category: str,
        *,
        previous_category: Optional[str] = None,
    ) -> None:
        """Update ledger row category; best-effort rename in store mappings."""
        new_category = (new_category or "").strip()
        if not new_category:
            raise ValueError("category required")
        with self._io_lock:
            self.load_stores()
            from ledger import load_ledger_transaction_by_stable_id
            from ledger import update_category_by_fingerprint

            row = load_ledger_transaction_by_stable_id(self._ledger_db_path, str(transaction_id))
            if row is None:
                raise ValueError("transaction not in ledger")
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

        lc = _ledger_display_context(row_data)
        pem = _payee_mapping_envelope_from_stores(self.stores_df, store_name)

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
                    ledger_id=lc["ledger_id"],
                    additional_detail=lc["additional_detail"],
                    notes=lc["notes"],
                    statement_month=lc["statement_month"],
                    row_fingerprint=lc["row_fingerprint"],
                    ingested_at=lc["ingested_at"],
                    payee_mapping=pem,
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
                ledger_id=lc["ledger_id"],
                additional_detail=lc["additional_detail"],
                notes=lc["notes"],
                statement_month=lc["statement_month"],
                row_fingerprint=lc["row_fingerprint"],
                ingested_at=lc["ingested_at"],
                payee_mapping=pem,
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
            ledger_id=lc["ledger_id"],
            additional_detail=lc["additional_detail"],
            notes=lc["notes"],
            statement_month=lc["statement_month"],
            row_fingerprint=lc["row_fingerprint"],
            ingested_at=lc["ingested_at"],
            payee_mapping=pem,
            transaction_id=transaction_id,
            prompt_id=pid,
        )

    def _persist_category_for_transaction(
        self, transaction_id: str, category: str, data: Optional[dict] = None
    ) -> None:
        from ledger import load_ledger_transaction_by_stable_id
        from ledger import update_category_and_notes_by_fingerprint
        from ledger import update_category_by_fingerprint

        row = load_ledger_transaction_by_stable_id(self._ledger_db_path, str(transaction_id))
        if row is None:
            raise ValueError("transaction not in ledger")
        fp = str(row.get("fingerprint", "")).strip()
        if not fp:
            raise ValueError("transaction has no fingerprint")
        with self._io_lock:
            if data is not None and "notes" in data:
                raw = data.get("notes")
                notes_str = "" if raw is None else str(raw).strip()
                if len(notes_str) > 8000:
                    notes_str = notes_str[:8000]
                update_category_and_notes_by_fingerprint(self._ledger_db_path, fp, category, notes_str)
            else:
                update_category_by_fingerprint(self._ledger_db_path, fp, category)

    def _apply_queue_notes_if_present(self, stable_id: str, data: dict) -> None:
        """When ``notes`` is present in queue JSON, persist to the ledger row for ``stable_id``."""
        if "notes" not in data:
            return
        from ledger import load_ledger_transaction_by_stable_id
        from ledger import update_notes_by_fingerprint

        row = load_ledger_transaction_by_stable_id(self._ledger_db_path, str(stable_id))
        if row is None:
            raise ValueError("transaction not in ledger")
        fp = str(row.get("fingerprint", "")).strip()
        if not fp:
            raise ValueError("transaction has no fingerprint")
        raw = data.get("notes")
        notes_str = "" if raw is None else str(raw).strip()
        if len(notes_str) > 8000:
            notes_str = notes_str[:8000]
        with self._io_lock:
            update_notes_by_fingerprint(self._ledger_db_path, fp, notes_str)

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
            self._persist_category_for_transaction(transaction_id, category_input, data)
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
            self._persist_category_for_transaction(transaction_id, str(category), data)
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
            self._persist_category_for_transaction(transaction_id, category_input, data)
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
        from ledger import load_ledger_transaction_by_stable_id

        ldb_row = load_ledger_transaction_by_stable_id(self._ledger_db_path, tid)
        if ldb_row is None:
            return "transaction not in ledger"
        store_name = str(ldb_row["מקור עסקה"])
        try:
            if kind == "fluid":
                new_cat = (data.get("category") or "").strip()
                if not new_cat:
                    return "category required"
                prev = str(ldb_row.get("קטגוריה", "") or "")
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
                prev_cat = str(ldb_row.get("קטגוריה", "") or "")
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
            self._apply_queue_notes_if_present(tid, data)
        except ValueError as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("queue revise failed: %s", e)
            return str(e)
        return None

    def count_rows_needing_category(self) -> int:
        """Rows still needing a manual category (live query on the ledger)."""
        from ledger import count_transactions_needing_manual_category

        return count_transactions_needing_manual_category(self._ledger_db_path)

    def load_known_transactions(self):
        """Category hints keyed by stable id (from ledger)."""
        from ledger import load_known_transactions_backup_from_ledger

        return load_known_transactions_backup_from_ledger(self._ledger_db_path)

    def auto_categorize(self):
        from ledger import count_ledger_transaction_rows
        from ledger import count_transactions_needing_manual_category
        from ledger import forward_fill_uncategorized_for_static_stores_sql
        from ledger import load_transactions_dataframe_from_ledger

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

    def _ledger_forward_fill_uncategorized_if_static(self, store_name: str, is_static: int) -> None:
        """Bulk-fill other uncategorized rows for this store when it is static (never overwrites set categories)."""
        if int(is_static) != 1:
            return
        from ledger import forward_fill_uncategorized_for_store_if_static_sql

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
