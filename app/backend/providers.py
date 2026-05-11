"""
Load/save ``data/private/providers.json`` — single source for portal + Google Sheets secrets,
plus lazy mapping from provider ids to Selenium portal classes (no Selenium import until used).

No runtime reads of legacy ``bank_*`` / ``GOOGLE_*`` environment variables; use
``import_legacy_env_from_dotenv()`` once to migrate.

CLI: ``python -m providers`` — merge legacy ``.env`` keys into ``providers.json``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional, Type

import config

log = logging.getLogger(__name__)

PROVIDERS_VERSION = 1

_DEFAULT_CREDENTIAL_IDS = ("max", "isracard")

# --- Lazy portal class maps (avoid importing Selenium for JSON-only callers) ---
_banks_cache: dict[str, Type] | None = None
_credits_cache: dict[str, Type] | None = None


def _portal_class_maps() -> tuple[dict[str, Type], dict[str, Type]]:
    global _banks_cache, _credits_cache
    if _banks_cache is not None and _credits_cache is not None:
        return _banks_cache, _credits_cache
    from pipeline.fetch import Bank as _Bank, IsracardCredit as _IsracardCredit, MaxCredit as _MaxCredit

    _banks_cache = {"leumi": _Bank}
    _credits_cache = {"max": _MaxCredit, "isracard": _IsracardCredit}
    return _banks_cache, _credits_cache


def bank_class(provider_id: str) -> Type:
    banks, _ = _portal_class_maps()
    pid = (provider_id or "").strip().lower()
    cls = banks.get(pid)
    if cls is None:
        raise ValueError(f"Unsupported bank provider {provider_id!r}; supported: {sorted(banks)}")
    return cls


def credit_provider_classes() -> dict[str, Type]:
    _, credits = _portal_class_maps()
    return credits


def assert_credit_provider(provider_id: str) -> None:
    credits = credit_provider_classes()
    pid = (provider_id or "").strip().lower()
    if pid not in credits:
        raise ValueError(f"Unsupported credit provider {provider_id!r}; supported: {sorted(credits)}")


def default_document() -> dict[str, Any]:
    return {
        "version": PROVIDERS_VERSION,
        "bank": {
            "provider": "leumi",
            "credentials": {"username": "", "password": ""},
        },
        "credit_cards": [
            {"id": "max", "enabled": True, "credentials": {"username": "", "password": ""}},
            {
                "id": "isracard",
                "enabled": True,
                "credentials": {"username": "", "password": "", "last6": ""},
            },
        ],
        "google_sheets": {"service_account_json_path": "", "worksheet_id": ""},
        "investment_portfolio": {"enabled": True},
    }


def providers_file_path() -> str:
    return config.providers_file


def _ensure_private_dir() -> str:
    path = providers_file_path()
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    return path


def load_document() -> dict[str, Any]:
    path = providers_file_path()
    if not os.path.isfile(path):
        return copy.deepcopy(default_document())
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("providers.json unreadable (%s), using defaults", e)
        return copy.deepcopy(default_document())
    if not isinstance(raw, dict):
        return copy.deepcopy(default_document())
    return normalize_document(raw)


def normalize_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Merge unknown / partial docs toward the default shape."""
    base = copy.deepcopy(default_document())

    bank = doc.get("bank") if isinstance(doc.get("bank"), dict) else {}
    bc = bank.get("credentials") if isinstance(bank.get("credentials"), dict) else {}
    base["bank"]["provider"] = str(bank.get("provider") or base["bank"]["provider"]).strip() or "leumi"
    base["bank"]["credentials"]["username"] = str(bc.get("username") or "")
    base["bank"]["credentials"]["password"] = str(bc.get("password") or "")

    incoming_cards = doc.get("credit_cards")
    if not isinstance(incoming_cards, list):
        incoming_cards = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in incoming_cards:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip().lower()
        if not cid:
            continue
        by_id[cid] = item

    merged_cards: list[dict[str, Any]] = []
    for cid in _DEFAULT_CREDENTIAL_IDS:
        inc = by_id.get(cid, {})
        cred = inc.get("credentials") if isinstance(inc.get("credentials"), dict) else {}
        entry = {
            "id": cid,
            "enabled": bool(inc.get("enabled", True)),
            "credentials": {
                "username": str(cred.get("username") or ""),
                "password": str(cred.get("password") or ""),
            },
        }
        if cid == "isracard":
            entry["credentials"]["last6"] = str(cred.get("last6") or "")
        merged_cards.append(entry)

    base["credit_cards"] = merged_cards

    gs = doc.get("google_sheets") if isinstance(doc.get("google_sheets"), dict) else {}
    base["google_sheets"]["service_account_json_path"] = str(gs.get("service_account_json_path") or "")
    base["google_sheets"]["worksheet_id"] = str(gs.get("worksheet_id") or "")

    inv = doc.get("investment_portfolio") if isinstance(doc.get("investment_portfolio"), dict) else {}
    base["investment_portfolio"]["enabled"] = bool(inv.get("enabled", True))

    base["version"] = PROVIDERS_VERSION
    return base


def save_document_atomic(doc: dict[str, Any]) -> None:
    path = _ensure_private_dir()
    normalized = normalize_document(doc)
    tmp = f"{path}.{os.getpid()}.tmp"
    data = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def _nonempty(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    t = str(s).strip()
    return t or None


def _expand_path(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    return os.path.normpath(os.path.expanduser(os.path.expandvars(p.strip())))


@dataclass(frozen=True)
class ResolvedProviders:
    bank_provider: str
    bank_username: Optional[str]
    bank_password: Optional[str]
    credit_max_enabled: bool
    max_username: Optional[str]
    max_password: Optional[str]
    credit_isracard_enabled: bool
    isracard_username: Optional[str]
    isracard_password: Optional[str]
    isracard_last6: Optional[str]
    investment_portfolio_enabled: bool
    google_service_account_json_path: Optional[str]
    google_worksheet_id: Optional[str]


def resolve_document(doc: dict[str, Any]) -> ResolvedProviders:
    doc = normalize_document(doc)
    bank = doc["bank"]
    bc = bank["credentials"]
    max_e = isracard_e = False
    mu = mp = iu = ip = il = None
    for card in doc["credit_cards"]:
        cid = str(card["id"])
        if cid == "max":
            max_e = bool(card.get("enabled"))
            c = card["credentials"]
            mu = _nonempty(c.get("username"))
            mp = _nonempty(c.get("password"))
        elif cid == "isracard":
            isracard_e = bool(card.get("enabled"))
            c = card["credentials"]
            iu = _nonempty(c.get("username"))
            ip = _nonempty(c.get("password"))
            il = _nonempty(c.get("last6"))
    gs = doc["google_sheets"]
    gpath = _nonempty(gs.get("service_account_json_path"))
    inv_doc = doc.get("investment_portfolio") if isinstance(doc.get("investment_portfolio"), dict) else {}
    inv_enabled = bool(inv_doc.get("enabled", True))
    return ResolvedProviders(
        bank_provider=str(bank.get("provider") or "leumi").strip() or "leumi",
        bank_username=_nonempty(bc.get("username")),
        bank_password=_nonempty(bc.get("password")),
        credit_max_enabled=max_e,
        max_username=mu,
        max_password=mp,
        credit_isracard_enabled=isracard_e,
        isracard_username=iu,
        isracard_password=ip,
        isracard_last6=il,
        investment_portfolio_enabled=inv_enabled,
        google_service_account_json_path=_expand_path(gpath) if gpath else None,
        google_worksheet_id=_nonempty(gs.get("worksheet_id")),
    )


def get_resolved() -> ResolvedProviders:
    return resolve_document(load_document())


def google_api_user_path() -> str:
    """Path to Google service account JSON (expanded); empty string if unset."""
    p = get_resolved().google_service_account_json_path
    return p or ""


def google_worksheet_id() -> str:
    return get_resolved().google_worksheet_id or ""


def _secret_merge(previous: str, incoming: Any) -> str:
    """
    - absent incoming key handled by caller
    - None -> clear
    - "" -> keep previous
    - str -> set
    """
    if incoming is None:
        return ""
    if incoming == "":
        return previous
    return str(incoming)


def merge_put_update(current: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    out = normalize_document(current)

    bank_in = body.get("bank")
    if isinstance(bank_in, dict):
        prov = bank_in.get("provider")
        if prov is not None:
            out["bank"]["provider"] = str(prov).strip() or out["bank"]["provider"]
        cred_in = bank_in.get("credentials")
        if isinstance(cred_in, dict):
            prev = out["bank"]["credentials"]
            if "username" in cred_in:
                out["bank"]["credentials"]["username"] = str(cred_in.get("username") or "")
            if "password" in cred_in:
                out["bank"]["credentials"]["password"] = _secret_merge(
                    prev["password"], cred_in.get("password")
                )

    cc_in = body.get("credit_cards")
    if isinstance(cc_in, list):
        by_id = {str(x.get("id", "")).lower(): x for x in cc_in if isinstance(x, dict)}
        for i, card in enumerate(out["credit_cards"]):
            cid = str(card["id"])
            inc = by_id.get(cid.lower())
            if not isinstance(inc, dict):
                continue
            if "enabled" in inc:
                out["credit_cards"][i]["enabled"] = bool(inc.get("enabled"))
            cred_in = inc.get("credentials")
            if isinstance(cred_in, dict):
                prev = out["credit_cards"][i]["credentials"]
                if "username" in cred_in:
                    out["credit_cards"][i]["credentials"]["username"] = str(cred_in.get("username") or "")
                if "password" in cred_in:
                    out["credit_cards"][i]["credentials"]["password"] = _secret_merge(
                        prev["password"], cred_in.get("password")
                    )
                if cid == "isracard" and "last6" in cred_in:
                    out["credit_cards"][i]["credentials"]["last6"] = _secret_merge(
                        str(prev.get("last6") or ""), cred_in.get("last6")
                    )

    gs_in = body.get("google_sheets")
    if isinstance(gs_in, dict):
        gs = out["google_sheets"]
        if "service_account_json_path" in gs_in:
            gs["service_account_json_path"] = str(gs_in.get("service_account_json_path") or "")
        if "worksheet_id" in gs_in:
            gs["worksheet_id"] = str(gs_in.get("worksheet_id") or "")

    ip_in = body.get("investment_portfolio")
    if isinstance(ip_in, dict) and "enabled" in ip_in:
        out["investment_portfolio"]["enabled"] = bool(ip_in.get("enabled"))

    return out


def document_for_api_get(doc: dict[str, Any]) -> dict[str, Any]:
    doc = normalize_document(doc)
    bank_c = doc["bank"]["credentials"]
    bank_out = {
        "provider": doc["bank"]["provider"],
        "credentials": {
            "username": bank_c.get("username") or "",
            "password_set": bool(str(bank_c.get("password") or "").strip()),
        },
    }
    cards_out: list[dict[str, Any]] = []
    for card in doc["credit_cards"]:
        c = card["credentials"]
        co: dict[str, Any] = {
            "username": c.get("username") or "",
            "password_set": bool(str(c.get("password") or "").strip()),
        }
        if card["id"] == "isracard":
            co["last6_set"] = bool(str(c.get("last6") or "").strip())
        cards_out.append({"id": card["id"], "enabled": bool(card.get("enabled")), "credentials": co})
    gs = doc["google_sheets"]
    inv = doc.get("investment_portfolio") if isinstance(doc.get("investment_portfolio"), dict) else {}
    return {
        "version": doc["version"],
        "bank": bank_out,
        "credit_cards": cards_out,
        "investment_portfolio": {"enabled": bool(inv.get("enabled", True))},
        "google_sheets": {
            "service_account_json_path": gs.get("service_account_json_path") or "",
            "worksheet_id": gs.get("worksheet_id") or "",
        },
        "providers_file": providers_file_path(),
    }


_LEGACY_ENV_KEYS = (
    "bank_username",
    "bank_password",
    "credit_username",
    "credit_last6",
    "credit_password",
    "max_username",
    "max_password",
    "GOOGLE_API_USER",
    "GOOGLE_WORKSHEET_ID",
)


def import_legacy_env_from_environ(env: dict[str, str]) -> dict[str, Any]:
    """Build a normalized document by merging legacy env-style keys (upper/lower)."""
    base = normalize_document(load_document())

    def g(*names: str) -> str:
        for n in names:
            v = env.get(n)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    base["bank"]["credentials"]["username"] = g("bank_username") or base["bank"]["credentials"]["username"]
    base["bank"]["credentials"]["password"] = g("bank_password") or base["bank"]["credentials"]["password"]

    for i, card in enumerate(base["credit_cards"]):
        cid = card["id"]
        if cid == "max":
            c = card["credentials"]
            c["username"] = g("max_username") or c["username"]
            c["password"] = g("max_password") or c["password"]
        elif cid == "isracard":
            c = card["credentials"]
            c["username"] = g("credit_username") or c["username"]
            c["password"] = g("credit_password") or c["password"]
            c["last6"] = g("credit_last6") or c["last6"]

    gs = base["google_sheets"]
    gs["service_account_json_path"] = g("GOOGLE_API_USER") or gs["service_account_json_path"]
    gs["worksheet_id"] = g("GOOGLE_WORKSHEET_ID") or gs["worksheet_id"]

    return normalize_document(base)


def import_legacy_env_from_dotenv(*, save: bool = True) -> dict[str, Any]:
    """Load ``.env`` from CWD via python-dotenv, merge into providers, optionally save."""
    from dotenv import dotenv_values

    root = os.getcwd()
    env_path = os.path.join(root, ".env")
    values = dotenv_values(env_path)
    flat: dict[str, str] = {k: str(v) if v is not None else "" for k, v in values.items()}
    # also fold in os.environ for keys that might be set without .env
    for k in _LEGACY_ENV_KEYS:
        if k not in flat or not str(flat.get(k, "")).strip():
            v = os.environ.get(k)
            if v is not None and str(v).strip():
                flat[k] = str(v).strip()
    merged = import_legacy_env_from_environ(flat)
    if save:
        save_document_atomic(merged)
    return merged


def main() -> int:
    """``python -m providers`` — migrate legacy ``.env`` keys into ``providers.json``."""
    import_legacy_env_from_dotenv(save=True)
    print(f"Wrote merged providers to {providers_file_path()!r}")
    print("Remove bank_*, credit_*, max_*, GOOGLE_API_USER, GOOGLE_WORKSHEET_ID from .env when done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
