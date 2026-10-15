#!/usr/bin/env python3
"""
Standalone OneZero login and a controlled data sample.

Run from the repo root::

    python tools/onezero_probe.py
    python tools/onezero_probe.py --month 2026-09
    python tools/onezero_probe.py --month 2026-09 --source cards
    python tools/onezero_probe.py --month 2026-09 --source both
    python tools/onezero_probe.py --list-accounts
    python tools/onezero_probe.py --month 2026-09 --account-id ACCOUNT_ID
    python tools/onezero_probe.py --new-sms

With no flags, the script reads one page of 50 checking movements.
``--month YYYY-MM`` keeps checking rows whose value date is in that month.
The movements query has no date argument, so the script reads older pages
until a page starts before that month.
``--source cards`` reads each active card for that billing month.
``--source both`` reads checking rows and card lines. The checking feed also
contains the monthly card settlement, so the same spending can appear twice.
``--list-accounts`` writes account types and the card list.
``--account-id`` selects an account from a previous sample. The default is
the first account.
``--new-sms`` discards the saved SMS token and sends one new code.
A rejected login does not send another code. Run the command again to request one.

Asks for email, password, phone, and an SMS code only when they are missing.
Writes ``data/private/onezero-probe/session.json`` (email, phone, otpToken,
accessToken) and ``sample.json``. The password is never stored. Tokens are
never printed.

Optional environment overrides: ONEZERO_EMAIL, ONEZERO_PASSWORD, ONEZERO_PHONE.
The movements host asks for a client certificate. Set ONEZERO_MTLS_CERT and
ONEZERO_MTLS_KEY to PEM file paths, or place client.crt and client.key in the
probe directory.

This script does not call the finance pipeline.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import getpass
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from curl_cffi import requests as curl_requests
from curl_cffi.const import CurlHttpVersion


def probe_dir() -> str:
    """Session files live under the workspace private directory."""
    import config

    return os.path.join(config.private_dir, "onezero-probe")


def session_path() -> str:
    return os.path.join(probe_dir(), "session.json")


def sample_path() -> str:
    return os.path.join(probe_dir(), "sample.json")

IDENTITY = "https://identity.tfd-bank.com/v1"
GRAPHQL_URL = "https://mobile.tfd-bank.com/mobile-graph/graphql"

HEBREW_WORDS = re.compile(
    r"""[\u0590-\u05FF][\u0590-\u05FF"'\\\-_ /]*[\u0590-\u05FF]"""
)
MONTH_TEXT = re.compile(r"^(\d{4})-(\d{2})$")
PAGE_LIMIT = 50
MAX_MOVEMENT_PAGES = 36
ACCOUNT_FIELDS = ("accountId", "accountType", "subType", "currency", "status")

GET_CUSTOMER = """
query GetCustomer {
  customer {
    portfolios {
      portfolioId
      portfolioNum
      accounts {
        accountId
        accountType
        subType
        currency
        status
      }
    }
  }
}
"""

GET_MOVEMENTS = """
query GetMovements(
  $portfolioId: String!
  $accountId: String!
  $pagination: PaginationInput!
  $language: BffLanguage!
) {
  movements(
    portfolioId: $portfolioId
    accountId: $accountId
    pagination: $pagination
    language: $language
  ) {
    movements {
      movementId
      valueDate
      movementTimestamp
      movementAmount
      movementCurrency
      movementType
      creditDebit
      description
    }
    pagination { cursor hasMore }
  }
}
"""

GET_CARDS_DETAILS = """
query GetCardsDetails($portfolioId: String!) {
  cardsDetailsV3(portfolioId: $portfolioId) {
    cardsDetails {
      baseDetails {
        cardId
        lastFourDigits
        cardType
        cardStatus
        currency
        provider
      }
    }
  }
}
"""

GET_CARD_ACTIVITY = """
query GetCardActivity($cardId: String!, $billingYear: Int!, $billingMonth: Int!) {
  cardActivityV3(cardId: $cardId, billingYear: $billingYear, billingMonth: $billingMonth) {
    transactions {
      transactionId
      merchantName
      purchaseDate
      paymentDate
      debitAmount { amount currency }
      originalAmount { amount currency }
      direction
      status
      type
      numberOfPayments
      paymentNumber
    }
  }
}
"""

PostFn = Callable[[str, dict[str, Any], dict[str, str] | None], tuple[int, Any]]


@dataclass(frozen=True)
class PullOptions:
    """What this run reads after login."""

    month: str | None = None
    month_from: str | None = None
    month_to: str | None = None
    source: str = "checking"
    account_id: str | None = None
    list_accounts: bool = False
    new_sms: bool = False


def _client_cert() -> tuple[str, str] | None:
    """PEM paths for the One Zero app client certificate, when present."""
    cert = os.environ.get("ONEZERO_MTLS_CERT", "").strip()
    key = os.environ.get("ONEZERO_MTLS_KEY", "").strip()
    if not cert and not key:
        default_cert = os.path.join(probe_dir(), "client.crt")
        default_key = os.path.join(probe_dir(), "client.key")
        if os.path.isfile(default_cert) and os.path.isfile(default_key):
            return default_cert, default_key
        return None
    if not cert or not key or not os.path.isfile(cert) or not os.path.isfile(key):
        print("ONEZERO_MTLS_CERT and ONEZERO_MTLS_KEY must both be paths to PEM files.")
        raise SystemExit(1)
    return cert, key


# One session so the Cloudflare __cf_bm cookie stays across the login calls.
# Chrome over HTTP/2 is blocked. The same impersonation over HTTP/3 is not.
_SESSION = curl_requests.Session(
    impersonate="chrome",
    http_version=CurlHttpVersion.V3,
    cert=_client_cert(),
)


class ProbeError(Exception):
    """A call failed. HTTP 403 is handled separately and stops the run."""


def sanitize_hebrew(text: str) -> str:
    """Undo OneZero's reversed Hebrew plus U+202D left-to-right override."""
    if "\u202d" not in text:
        return text.strip()
    plain = text.replace("\u202d", "").strip()
    parts: list[str] = []
    index = 0
    for match in HEBREW_WORDS.finditer(plain):
        start, end = match.start(), match.end()
        parts.append(plain[index:start])
        parts.append(plain[start:end][::-1])
        index = end
    parts.append(plain[index:])
    return "".join(parts)


def _clip(text: str) -> str:
    """A short bank message. Token-shaped and long values stay out of the log."""
    text = text.strip()
    if not text or text.startswith("eyJ") or len(text) > 200:
        return ""
    if "\n" in text or "\r" in text:
        return ""
    return text


def _safe_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    parts: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, dict):
            value = value.get("message") or value.get("errorMessage") or value.get("type")
        if not isinstance(value, str):
            return
        clipped = _clip(value)
        if clipped and clipped not in parts:
            parts.append(clipped)

    error_response = body.get("errorResponse")
    if isinstance(error_response, dict):
        add(error_response.get("type"))
        add(error_response.get("description"))
        add(error_response.get("message"))
        add(error_response.get("errorMessage"))
    for key in ("errorMessage", "message", "error", "title", "detail", "errorCode"):
        add(body.get(key))
    return ": ".join(parts)


def otp_expired(token: str) -> bool:
    """True when the SMS token carries an ``exp`` claim that has passed."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    payload = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return False
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)):
        return False
    return float(exp) <= time.time() + 30


def refresh_otp_after(exc: ProbeError) -> bool:
    """A 401 keeps the SMS token. A revoke or a 5xx means that token is finished."""
    text = str(exc)
    if "ErrorIdTokenRevoke" in text:
        return True
    return text.startswith("HTTP 5")


def _default_post(url: str, payload: dict[str, Any], headers: dict[str, str] | None) -> tuple[int, Any]:
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    response = _SESSION.post(url, json=payload, headers=hdrs, timeout=60)
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text if response.status_code == 403 else None
    return response.status_code, body


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    post: PostFn = _default_post,
    announce: bool = True,
) -> Any:
    status, body = post(url, payload, headers)
    if status == 403:
        if isinstance(body, str) and (
            "cloudflare" in body.lower() or "Attention Required" in body
        ):
            print(f"Cloudflare bot block — impersonation rejected ({url})")
            if _SESSION.cert is None and "mobile.tfd-bank.com" in url:
                print(
                    "This host asks for a client certificate. "
                    "Set ONEZERO_MTLS_CERT and ONEZERO_MTLS_KEY to the app PEM files."
                )
        else:
            print(f"HTTP 403 from {url}")
        raise SystemExit(1)
    if status != 200:
        if announce:
            print(f"HTTP {status} from {url}")
        message = _safe_message(body)
        raise ProbeError(message or f"HTTP {status}")
    if isinstance(body, dict) and body.get("errors"):
        err = body["errors"][0]
        text = err.get("message") if isinstance(err, dict) else str(err)
        text = (text or "GraphQL error").strip()
        if len(text) > 300:
            text = text[:300]
        raise ProbeError(text)
    return body


def result_data(body: Any, label: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ProbeError(f"{label}: response was not a JSON object")
    data = body.get("resultData")
    if not isinstance(data, dict):
        message = _safe_message(body) or f"{label}: resultData missing"
        raise ProbeError(message)
    return data


def load_session() -> dict[str, str]:
    path = session_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read session file: {exc}")
        raise SystemExit(1) from exc
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("email", "phone", "otpToken", "accessToken"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def save_session(session: dict[str, str]) -> None:
    directory = probe_dir()
    os.makedirs(directory, exist_ok=True)
    payload = {
        "email": session.get("email") or "",
        "phone": session.get("phone") or "",
        "otpToken": session.get("otpToken") or "",
        "accessToken": session.get("accessToken") or "",
    }
    path = session_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _prompt(label: str) -> str:
    while True:
        value = input(label).strip()
        if value:
            return value
        print("A value is required.")


def _prompt_password() -> str:
    while True:
        value = getpass.getpass("Password: ").strip()
        if value:
            return value
        print("A value is required.")


def resolve_email(session: dict[str, str]) -> str:
    env = os.environ.get("ONEZERO_EMAIL", "").strip()
    if env:
        session["email"] = env
        return env
    if session.get("email"):
        return session["email"]
    email = _prompt("Email: ")
    session["email"] = email
    save_session(session)
    return email


def resolve_password() -> str:
    env = os.environ.get("ONEZERO_PASSWORD", "").strip()
    if env:
        return env
    return _prompt_password()


def resolve_phone(session: dict[str, str]) -> str:
    env = os.environ.get("ONEZERO_PHONE", "").strip()
    phone = env or session.get("phone") or ""
    while not phone.startswith("+"):
        if phone:
            print("Phone must start with + and a country code, for example +972…")
        phone = _prompt("Phone (+972…): ")
    session["phone"] = phone
    save_session(session)
    return phone


_otp_lock = threading.Lock()
_OTP_BLOCK_SECONDS = 15 * 60


def _otp_block_path() -> str:
    return os.path.join(probe_dir(), "otp-block.txt")


def _otp_wait_seconds() -> int:
    path = _otp_block_path()
    try:
        with open(path, encoding="utf-8") as handle:
            until = float(handle.read().strip())
    except (OSError, ValueError):
        return 0
    remaining = int(until - time.time())
    return remaining if remaining > 0 else 0


def _arm_otp_block() -> None:
    path = _otp_block_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _otp_lock:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(time.time() + _OTP_BLOCK_SECONDS))
            handle.write("\n")


def prepare_sms(session: dict[str, str], *, post: PostFn = _default_post) -> str:
    """Send an SMS and return the short-lived ``otpContext``. The code is not stored."""
    phone = session.get("phone") or ""
    if not phone.startswith("+"):
        raise ProbeError("Phone must start with + and a country code.")
    remaining = _otp_wait_seconds()
    if remaining:
        raise ProbeError(
            f"Max send attempts reached. Wait {remaining} seconds before another SMS request."
        )
    device_body = post_json(
        f"{IDENTITY}/devices/token",
        {"extClientId": "mobile", "os": "Android"},
        post=post,
    )
    device_token = result_data(device_body, "devices/token").get("deviceToken")
    if not isinstance(device_token, str) or not device_token:
        raise ProbeError("devices/token: deviceToken missing")
    try:
        prepare_body = post_json(
            f"{IDENTITY}/otp/prepare",
            {"factorValue": phone, "deviceToken": device_token, "otpChannel": "SMS_OTP"},
            post=post,
        )
    except ProbeError as exc:
        if "Max send attempts" in str(exc):
            _arm_otp_block()
        raise
    otp_context = result_data(prepare_body, "otp/prepare").get("otpContext")
    if not isinstance(otp_context, str) or not otp_context:
        raise ProbeError("otp/prepare: otpContext missing")
    return otp_context


def verify_sms(session: dict[str, str], otp_context: str, code: str, *, post: PostFn = _default_post) -> str:
    """Exchange an SMS code for an otpToken and save that token."""
    verify_body = post_json(
        f"{IDENTITY}/otp/verify",
        {"otpContext": otp_context, "otpCode": code},
        post=post,
    )
    otp_token = result_data(verify_body, "otp/verify").get("otpToken")
    if not isinstance(otp_token, str) or not otp_token:
        raise ProbeError("otp/verify: otpToken missing")
    session["otpToken"] = otp_token
    save_session(session)
    return otp_token


def obtain_otp_token(session: dict[str, str], *, post: PostFn = _default_post) -> str:
    phone = resolve_phone(session)
    print("Requesting device token")
    print(f"Sending SMS to {phone}")
    otp_context = prepare_sms(session, post=post)
    code = _prompt("SMS code: ")
    otp_token = verify_sms(session, otp_context, code, post=post)
    print("OTP token saved")
    return otp_token


def login(email: str, password: str, otp_token: str, *, post: PostFn = _default_post) -> str:
    print("Requesting id token")
    id_body = post_json(
        f"{IDENTITY}/getIdToken",
        {"otpSmsToken": otp_token, "email": email, "pass": password, "pinCode": ""},
        post=post,
    )
    id_token = result_data(id_body, "getIdToken").get("idToken")
    if not isinstance(id_token, str) or not id_token:
        raise ProbeError("getIdToken: idToken missing")

    print("Requesting session token")
    session_body = post_json(
        f"{IDENTITY}/sessions/token",
        {"idToken": id_token, "pass": password},
        post=post,
    )
    access = result_data(session_body, "sessions/token").get("accessToken")
    if not isinstance(access, str) or not access:
        raise ProbeError("sessions/token: accessToken missing")
    return access


def access_token_works(access_token: str, *, post: PostFn = _default_post) -> bool:
    """True when the saved access token can read the customer query."""
    if not access_token:
        return False
    try:
        graphql(access_token, GET_CUSTOMER, {}, post=post, announce=False)
    except ProbeError:
        return False
    return True


def graphql(
    access_token: str,
    query: str,
    variables: dict[str, Any],
    *,
    post: PostFn = _default_post,
    announce: bool = True,
) -> Any:
    body = post_json(
        GRAPHQL_URL,
        {"operationName": None, "query": query, "variables": variables},
        headers={"authorization": f"Bearer {access_token}"},
        post=post,
        announce=announce,
    )
    if not isinstance(body, dict) or "data" not in body:
        raise ProbeError("GraphQL response did not include data")
    return body["data"]


def parse_month(text: str) -> tuple[int, int, str, str]:
    """Return year, month, first day, and last day for ``YYYY-MM``."""
    match = MONTH_TEXT.fullmatch(text.strip())
    if not match:
        raise ProbeError("Month must be YYYY-MM, for example 2026-09.")
    year = int(match.group(1))
    month = int(match.group(2))
    if month < 1 or month > 12:
        raise ProbeError("Month must be YYYY-MM, for example 2026-09.")
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{last_day:02d}"
    return year, month, start, end


def _month_arg(text: str) -> str:
    try:
        parse_month(text)
    except ProbeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return text.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/onezero_probe.py",
        description="OneZero login and a controlled data sample.",
    )
    parser.add_argument(
        "--month",
        type=_month_arg,
        help="YYYY-MM. Checking rows use the value date. Card rows use this billing month.",
    )
    parser.add_argument("--from", dest="month_from", type=_month_arg, help="Backfill start YYYY-MM.")
    parser.add_argument("--to", dest="month_to", type=_month_arg, help="Backfill end YYYY-MM.")
    parser.add_argument(
        "--source",
        choices=("checking", "cards", "both"),
        default="checking",
        help="checking reads account movements. cards reads card lines. both reads both.",
    )
    parser.add_argument(
        "--account-id",
        help="Account id from a previous sample. The default is the first account.",
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="Write account types and the card list.",
    )
    parser.add_argument(
        "--new-sms",
        action="store_true",
        help="Discard the saved SMS token and send a new code.",
    )
    return parser


def options_from_args(argv: list[str] | None = None) -> PullOptions:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_accounts and (args.month or args.month_from or args.month_to):
        parser.error("--list-accounts writes the account list. Omit --month.")
    if bool(args.month_from) != bool(args.month_to):
        parser.error("Backfill needs both --from and --to.")
    if args.month and (args.month_from or args.month_to):
        parser.error("Use --month or --from/--to, not both.")
    if (
        args.source in ("cards", "both")
        and not args.month
        and not args.month_from
        and not args.list_accounts
    ):
        parser.error("--month is required when --source is cards or both")
    account_id = args.account_id.strip() if isinstance(args.account_id, str) else None
    return PullOptions(
        month=args.month,
        month_from=args.month_from,
        month_to=args.month_to,
        source=args.source,
        account_id=account_id or None,
        list_accounts=args.list_accounts,
        new_sms=args.new_sms,
    )


def portfolios_from_customer(data: Any) -> list[dict[str, Any]]:
    customers = data.get("customer") if isinstance(data, dict) else None
    if isinstance(customers, dict):
        customers = [customers]
    portfolios: list[dict[str, Any]] = []
    for customer in customers or []:
        if not isinstance(customer, dict):
            continue
        for item in customer.get("portfolios") or []:
            if isinstance(item, dict):
                portfolios.append(item)
    return portfolios


def public_account(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: raw.get(key) for key in ACCOUNT_FIELDS}


def portfolio_summary(portfolio: dict[str, Any]) -> dict[str, Any]:
    accounts = [
        public_account(account)
        for account in portfolio.get("accounts") or []
        if isinstance(account, dict)
    ]
    return {
        "portfolioId": portfolio.get("portfolioId"),
        "portfolioNum": portfolio.get("portfolioNum"),
        "accounts": accounts,
    }


def select_account(
    portfolios: list[dict[str, Any]],
    account_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if account_id:
        for portfolio in portfolios:
            for account in portfolio.get("accounts") or []:
                if isinstance(account, dict) and account.get("accountId") == account_id:
                    return portfolio, account
        return None
    for portfolio in portfolios:
        for account in portfolio.get("accounts") or []:
            if isinstance(account, dict) and account.get("accountId"):
                return portfolio, account
    return None


def day_of(value: Any) -> str:
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return ""


def public_movement(row: dict[str, Any]) -> dict[str, Any]:
    description = row.get("description")
    return {
        "movementId": row.get("movementId"),
        "valueDate": row.get("valueDate"),
        "movementTimestamp": row.get("movementTimestamp"),
        "movementType": row.get("movementType"),
        "creditDebit": row.get("creditDebit"),
        "movementAmount": row.get("movementAmount"),
        "movementCurrency": row.get("movementCurrency"),
        "description": sanitize_hebrew(description) if isinstance(description, str) else "",
    }


def movement_day(row: dict[str, Any]) -> str:
    """Value date when present. Movement time is the fallback."""
    return day_of(row.get("valueDate")) or day_of(row.get("movementTimestamp"))


def in_month(row: dict[str, Any], start: str, end: str) -> bool:
    day = movement_day(row)
    return bool(day) and start <= day <= end


def fetch_movements(
    access_token: str,
    portfolio_id: Any,
    account_id: Any,
    *,
    month: str | None,
    post: PostFn,
    stop_before: str | None = None,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Read checking movements.

    A month walks older pages, then keeps that month.
    ``stop_before`` is the first day of a backfill range. The walk stops when a
    page starts before that day and keeps rows on or after it.
    """
    start = ""
    end = ""
    if month:
        _year, _mon, start, end = parse_month(month)
    floor = stop_before or start
    page_cap = MAX_MOVEMENT_PAGES if max_pages is None else max_pages
    cursor: str | None = None
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for _ in range(page_cap):
        page = graphql(
            access_token,
            GET_MOVEMENTS,
            {
                "portfolioId": portfolio_id,
                "accountId": account_id,
                "language": "HEBREW",
                "pagination": {"cursor": cursor, "limit": PAGE_LIMIT},
            },
            post=post,
        )
        block = page.get("movements") if isinstance(page, dict) else None
        raw_rows = block.get("movements") if isinstance(block, dict) else None
        page_rows = [public_movement(row) for row in raw_rows or [] if isinstance(row, dict)]
        if not page_rows:
            break
        rows.extend(page_rows)
        if not month and not stop_before:
            break
        days = [day for day in (movement_day(row) for row in page_rows) if day]
        if days and floor and min(days) < floor:
            break
        pagination = block.get("pagination") if isinstance(block, dict) else None
        has_more = bool(isinstance(pagination, dict) and pagination.get("hasMore"))
        nxt = pagination.get("cursor") if isinstance(pagination, dict) else None
        if not has_more or not isinstance(nxt, str) or not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    if start and end:
        rows = [row for row in rows if in_month(row, start, end)]
    elif stop_before:
        rows = [row for row in rows if movement_day(row) >= stop_before]
    return rows


def public_card(raw: dict[str, Any], portfolio_id: Any) -> dict[str, Any]:
    base = raw.get("baseDetails") if isinstance(raw.get("baseDetails"), dict) else raw
    if not isinstance(base, dict):
        base = {}
    return {
        "portfolioId": portfolio_id,
        "cardId": base.get("cardId"),
        "lastFourDigits": base.get("lastFourDigits"),
        "cardType": base.get("cardType"),
        "cardStatus": base.get("cardStatus"),
        "currency": base.get("currency"),
        "provider": base.get("provider"),
    }


def _amount_parts(value: Any) -> tuple[Any, Any]:
    if not isinstance(value, dict):
        return None, None
    return value.get("amount"), value.get("currency")


def public_card_txn(raw: dict[str, Any]) -> dict[str, Any]:
    merchant = raw.get("merchantName")
    debit_amount, debit_currency = _amount_parts(raw.get("debitAmount"))
    original_amount, original_currency = _amount_parts(raw.get("originalAmount"))
    return {
        "transactionId": raw.get("transactionId"),
        "merchantName": sanitize_hebrew(merchant) if isinstance(merchant, str) else "",
        "purchaseDate": raw.get("purchaseDate"),
        "paymentDate": raw.get("paymentDate"),
        "debitAmount": debit_amount,
        "debitCurrency": debit_currency,
        "originalAmount": original_amount,
        "originalCurrency": original_currency,
        "direction": raw.get("direction"),
        "status": raw.get("status"),
        "type": raw.get("type"),
        "numberOfPayments": raw.get("numberOfPayments"),
        "paymentNumber": raw.get("paymentNumber"),
    }


def fetch_cards_details(
    access_token: str,
    portfolio_id: Any,
    *,
    post: PostFn,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        data = graphql(
            access_token,
            GET_CARDS_DETAILS,
            {"portfolioId": portfolio_id},
            post=post,
        )
    except ProbeError as exc:
        return [], str(exc)
    block = data.get("cardsDetailsV3") if isinstance(data, dict) else None
    raw_cards = block.get("cardsDetails") if isinstance(block, dict) else None
    cards = [public_card(item, portfolio_id) for item in raw_cards or [] if isinstance(item, dict)]
    return cards, None


def fetch_card_month(
    access_token: str,
    card: dict[str, Any],
    year: int,
    month: int,
    *,
    post: PostFn,
) -> dict[str, Any]:
    out = dict(card)
    out["billingYear"] = year
    out["billingMonth"] = month
    if card.get("cardStatus") != "ACTIVE":
        out["transactions"] = []
        out["transactionCount"] = 0
        return out
    try:
        data = graphql(
            access_token,
            GET_CARD_ACTIVITY,
            {
                "cardId": card.get("cardId"),
                "billingYear": year,
                "billingMonth": month,
            },
            post=post,
        )
    except ProbeError as exc:
        out["transactions"] = []
        out["transactionCount"] = 0
        out["error"] = str(exc)
        return out
    block = data.get("cardActivityV3") if isinstance(data, dict) else None
    raw_txns = block.get("transactions") if isinstance(block, dict) else None
    txns = [public_card_txn(item) for item in raw_txns or [] if isinstance(item, dict)]
    out["transactions"] = txns
    out["transactionCount"] = len(txns)
    return out


def load_cards(
    access_token: str,
    portfolios: list[dict[str, Any]],
    *,
    month: str | None,
    with_activity: bool,
    post: PostFn,
) -> tuple[list[dict[str, Any]], str | None]:
    billing_year = 0
    billing_month = 0
    if with_activity:
        if not month:
            raise ProbeError("A month is required for card activity.")
        billing_year, billing_month, _start, _end = parse_month(month)
    cards_out: list[dict[str, Any]] = []
    errors: list[str] = []
    for portfolio in portfolios:
        found, err = fetch_cards_details(access_token, portfolio.get("portfolioId"), post=post)
        if err:
            errors.append(err)
            continue
        if not with_activity:
            cards_out.extend(found)
            continue
        for card in found:
            cards_out.append(
                fetch_card_month(access_token, card, billing_year, billing_month, post=post)
            )
    return cards_out, ("; ".join(errors) if errors else None)


def fetch_sample(
    access_token: str,
    *,
    month: str | None = None,
    source: str = "checking",
    account_id: str | None = None,
    list_accounts: bool = False,
    post: PostFn = _default_post,
) -> dict[str, Any]:
    if source not in ("checking", "cards", "both"):
        raise ProbeError("Source must be checking, cards, or both.")
    data = graphql(access_token, GET_CUSTOMER, {}, post=post)
    portfolios = portfolios_from_customer(data)
    selected = select_account(portfolios, account_id)
    if account_id and selected is None:
        raise ProbeError("No account matches that id.")
    if selected is None:
        print("No portfolio with an account was returned")
        return {
            "portfolioNum": None,
            "portfolioId": None,
            "accountId": None,
            "portfolios": [],
            "source": "accounts" if list_accounts else source,
            "month": month,
            "movementCount": 0,
            "movements": [],
        }

    portfolio, account = selected
    sample: dict[str, Any] = {
        "portfolioNum": portfolio.get("portfolioNum"),
        "portfolioId": portfolio.get("portfolioId"),
        "accountId": account.get("accountId"),
        "portfolios": [portfolio_summary(item) for item in portfolios],
        "source": "accounts" if list_accounts else source,
        "month": month,
    }
    card_portfolios = portfolios if list_accounts else [portfolio]
    if list_accounts or source in ("cards", "both"):
        cards, err = load_cards(
            access_token,
            card_portfolios,
            month=month,
            with_activity=not list_accounts,
            post=post,
        )
        sample["cards"] = cards
        if err and source == "cards" and not cards:
            raise ProbeError(err)
        if err:
            sample["cardsError"] = err
    if list_accounts or source == "cards":
        return sample

    rows = fetch_movements(
        access_token,
        portfolio.get("portfolioId"),
        account.get("accountId"),
        month=month,
        post=post,
    )
    sample["movementCount"] = len(rows)
    sample["movements"] = rows
    return sample


def _account_label(account_id: Any) -> str:
    """A console label. Account ids from this bank are tokens, so they stay in the sample file."""
    if not isinstance(account_id, str) or not account_id:
        return "account"
    if account_id.startswith("eyJ") or account_id.count(".") >= 2:
        return "account"
    if len(account_id) > 12:
        return f"account …{account_id[-4:]}"
    return f"account {account_id}"


def print_sample(sample: dict[str, Any]) -> None:
    print(f"Portfolio {sample.get('portfolioNum')}")
    selected = sample.get("accountId")
    for portfolio in sample.get("portfolios") or []:
        for account in portfolio.get("accounts") or []:
            mark = " selected" if account.get("accountId") == selected else ""
            print(
                f"  {_account_label(account.get('accountId'))}  "
                f"{account.get('accountType') or ''}  "
                f"{account.get('currency') or ''}  "
                f"{account.get('status') or ''}{mark}"
            )
    if "movements" in sample:
        count = int(sample.get("movementCount") or 0)
        month = sample.get("month")
        if month:
            print(f"{count} checking movements in {month}")
        else:
            print(f"{count} checking movements on this page")
        rows = sample.get("movements") or []
        for row in rows[:5]:
            print(
                f"  {row.get('valueDate')}  {row.get('creditDebit')}  "
                f"{row.get('movementAmount')} {row.get('movementCurrency') or ''}  "
                f"{row.get('description')}"
            )
        extra = count - min(count, 5)
        if extra:
            print(f"... and {extra} more in {sample_path()}")
    cards = sample.get("cards")
    if isinstance(cards, list):
        print(f"{len(cards)} cards")
        for card in cards:
            last4 = card.get("lastFourDigits") or ""
            status = card.get("cardStatus") or ""
            txn_count = card.get("transactionCount")
            if txn_count is None:
                print(f"  *{last4}  {status}")
            else:
                print(f"  *{last4}  {status}  {txn_count} lines")
            if card.get("error"):
                print(f"    {card['error']}")
    if sample.get("cardsError"):
        print(f"Card list: {sample['cardsError']}")


def write_sample(sample: dict[str, Any]) -> None:
    directory = probe_dir()
    os.makedirs(directory, exist_ok=True)
    path = sample_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(sample, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)
    print(f"Wrote {path}")


def _confirm_one_sms() -> bool:
    answer = input("Send one SMS now? Type yes: ").strip().lower()
    return answer == "yes"


def interactive_access(*, options: PullOptions | None = None, post: PostFn = _default_post) -> str:
    options = options or PullOptions()
    session = load_session()
    email = resolve_email(session)
    password = resolve_password()
    if options.new_sms or (session.get("otpToken") and otp_expired(session["otpToken"])):
        if session.get("otpToken") and otp_expired(session["otpToken"]):
            print("Saved SMS token has expired.")
        session.pop("otpToken", None)
        session.pop("accessToken", None)
        save_session(session)

    if not session.get("otpToken"):
        if options.new_sms or _confirm_one_sms():
            obtain_otp_token(session, post=post)
        else:
            raise ProbeError("No SMS was sent.")

    try:
        access = login(email, password, session["otpToken"], post=post)
    except ProbeError as exc:
        if str(exc).startswith("HTTP 401") or "401" in str(exc):
            print("The bank rejected the email or the password. The SMS token is still saved.")
        raise

    session["accessToken"] = access
    save_session(session)
    print("Access token saved")
    return access


def run(*, options: PullOptions | None = None, post: PostFn = _default_post) -> None:
    options = options or PullOptions()
    access = interactive_access(options=options, post=post)
    sample = fetch_sample(
        access,
        month=options.month,
        source=options.source,
        account_id=options.account_id,
        list_accounts=options.list_accounts,
        post=post,
    )
    write_sample(sample)
    print_sample(sample)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    options = options_from_args()
    try:
        if options.month_from and options.month_to:
            from ledger.api_sync import cli_backfill

            cli_backfill(options)
        else:
            run(options=options)
    except ProbeError as exc:
        print(str(exc))
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
