"""Load/save ``data/private/transaction_drop_rules.json`` — column/value pairs dropped at normalize time."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable

import config

log = logging.getLogger(__name__)

RULES_VERSION = 1

# Default pairs: legacy "core" list then "UI extra" list (same order as former compile split).
_DEFAULT_PAIR_ORDER: list[tuple[str, str]] = [
    ("מקור עסקה", "כרטיס דביט"),
    ('מקור עסקה', 'ישראכרט בע"מ-י'),
    ('מקור עסקה', "מקס איט פיננ-י"),
    ("מקור עסקה", "פקדון אינטר700"),
    ("מקור עסקה", "קניה-אינטרנט"),
    ("מקור עסקה", "מכירה-אינטרנט"),
    ("מקור עסקה", "פקדון אינטרנט"),
    ("מקור עסקה", "פקדון*"),
    ('מקור עסקה', 'קנית ני"ע'),
    ('מקור עסקה', 'מכירת ני"ע'),
    ('מקור עסקה', 'שינוי בנ"ע'),
    ('מקור עסקה', 'קנית ני""ע'),
    ('מקור עסקה', 'החלפת נייר ערך'),
    ('מקור עסקה', 'המרה אינטרנט'),
]


def default_document() -> dict[str, Any]:
    seen: set[tuple[str, str]] = set()
    rules: list[dict[str, str]] = []
    for col, val in _DEFAULT_PAIR_ORDER:
        key = (col, val)
        if key in seen:
            continue
        seen.add(key)
        rules.append({"column": col, "value": val})
    return {"version": RULES_VERSION, "rules": rules}


def validate_document(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("document must be a JSON object")
    raw_ver = doc.get("version", RULES_VERSION)
    try:
        ver = int(raw_ver)
    except (TypeError, ValueError) as e:
        raise ValueError("version must be an integer") from e
    if ver != RULES_VERSION:
        raise ValueError(f"unsupported version (expected {RULES_VERSION})")
    rules = doc.get("rules")
    if not isinstance(rules, list):
        raise ValueError("rules must be an array")
    out: list[dict[str, str]] = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise ValueError(f"rules[{i}] must be an object")
        extra = set(r.keys()) - {"column", "value"}
        if extra:
            raise ValueError(f"rules[{i}] has unknown keys: {sorted(extra)}")
        col = str(r.get("column", "")).strip()
        val = str(r.get("value", "")).strip()
        if not col or not val:
            raise ValueError(f"rules[{i}] needs non-empty column and value")
        out.append({"column": col, "value": val})
    return {"version": RULES_VERSION, "rules": out}


def pairs_from_document(doc: dict[str, Any]) -> list[tuple[str, str]]:
    v = validate_document(doc)
    return [(str(r["column"]), str(r["value"])) for r in v["rules"]]


def _rules_dir() -> str:
    return os.path.dirname(config.transaction_drop_rules_file)


def _ensure_rules_dir() -> None:
    os.makedirs(_rules_dir(), exist_ok=True)


def save_document_atomic(doc: dict[str, Any]) -> None:
    _ensure_rules_dir()
    normalized = validate_document(doc)
    path = config.transaction_drop_rules_file
    tmp = f"{path}.{os.getpid()}.tmp"
    data = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def materialize_default_file_if_missing() -> None:
    path = config.transaction_drop_rules_file
    if os.path.isfile(path):
        return
    log.info("transaction drop rules: creating default file %s", path)
    save_document_atomic(default_document())


def load_document_from_disk() -> dict[str, Any]:
    materialize_default_file_if_missing()
    path = config.transaction_drop_rules_file
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read transaction drop rules: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError("transaction drop rules file must contain a JSON object")
    return validate_document(raw)


def transaction_drop_pairs_from_file() -> list[tuple[str, str]]:
    return pairs_from_document(load_document_from_disk())


def transaction_drop_pairs(drop_sources: Iterable[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    if drop_sources is not None:
        return list(drop_sources)
    return transaction_drop_pairs_from_file()


def append_rule_if_absent(column: str, value: str) -> bool:
    """Append ``{column, value}`` if not already present. Returns True when a new rule was added."""
    col = str(column or "").strip()
    val = str(value or "").strip()
    if not col or not val:
        return False
    doc = load_document_from_disk()
    pairs = pairs_from_document(doc)
    if (col, val) in pairs:
        return False
    doc["rules"].append({"column": col, "value": val})
    save_document_atomic(doc)
    return True
