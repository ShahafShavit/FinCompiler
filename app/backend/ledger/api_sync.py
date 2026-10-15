"""Map an API fetcher onto ledger_transaction and run sync or backfill."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any, Callable

from integrations.onezero import (
    GET_CUSTOMER,
    PostFn,
    ProbeError,
    PullOptions,
    _default_post,
    access_token_works,
    day_of,
    fetch_card_month,
    fetch_movements,
    fetch_sample,
    graphql,
    in_month,
    interactive_access,
    load_cards,
    load_session,
    login,
    movement_day,
    otp_expired,
    parse_month,
    portfolios_from_customer,
    prepare_sms,
    save_session,
    select_account,
    verify_sms,
)
from ledger.books import book_spec
from ledger.store import connect_ledger, ingested_at_for_new_ledger_row, migrate_ledger_db

Sink = Callable[[str], None]
BACKFILL_MAX_PAGES = 400
_CHALLENGE_SECONDS = 600

_pending_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}


class BookSyncError(Exception):
    pass


def _sink_drop(_msg: str) -> None:
    return None


def api_fingerprint(external_id: str) -> str:
    digest = hashlib.sha256(external_id.encode("utf-8")).hexdigest()
    return f"api:{digest}"


def is_card_settlement(movement: dict[str, Any]) -> bool:
    """Checking rows that settle the card. Card lines stay in the totals."""
    kind = str(movement.get("movementType") or "").upper()
    description = str(movement.get("description") or "")
    if "CARD" in kind:
        return True
    return "כרטיס" in description


def _money(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return abs(float(value))
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    return abs(float(text))


def _split_amount(direction: Any, amount: Any) -> tuple[float, float]:
    value = _money(amount)
    if str(direction or "").upper() == "CREDIT":
        return 0.0, value
    return value, 0.0


def _movement_ledger_row(movement: dict[str, Any]) -> dict[str, Any] | None:
    day = movement_day(movement)
    external_id = movement.get("movementId")
    if len(day) < 10 or not isinstance(external_id, str) or not external_id:
        return None
    debit, credit = _split_amount(movement.get("creditDebit"), movement.get("movementAmount"))
    return {
        "date": day[:10],
        "debit": debit,
        "credit": credit,
        "merchant": str(movement.get("description") or ""),
        "detail": str(movement.get("movementType") or ""),
        "extra": "",
        "last4": "",
        "fingerprint": api_fingerprint(external_id),
        "statement_month": day[:7],
        "excluded": 1 if is_card_settlement(movement) else 0,
    }


def _card_ledger_row(txn: dict[str, Any], last_four: str, billing_month: str) -> dict[str, Any] | None:
    day = day_of(txn.get("purchaseDate")) or day_of(txn.get("paymentDate"))
    external_id = txn.get("transactionId")
    if len(day) < 10 or not isinstance(external_id, str) or not external_id:
        return None
    debit, credit = _split_amount(txn.get("direction"), txn.get("debitAmount"))
    payments = txn.get("numberOfPayments")
    payment_no = txn.get("paymentNumber")
    detail = ""
    if payments and payment_no:
        detail = f"{payment_no}/{payments}"
    return {
        "date": day[:10],
        "debit": debit,
        "credit": credit,
        "merchant": str(txn.get("merchantName") or ""),
        "detail": detail,
        "extra": str(txn.get("type") or ""),
        "last4": last_four,
        "fingerprint": api_fingerprint(external_id),
        "statement_month": billing_month,
        "excluded": 0,
    }


def rows_from_sample(sample: dict[str, Any], month: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for movement in sample.get("movements") or []:
        if isinstance(movement, dict):
            row = _movement_ledger_row(movement)
            if row is not None:
                rows.append(row)
    for card in sample.get("cards") or []:
        if not isinstance(card, dict):
            continue
        last_four = str(card.get("lastFourDigits") or "")
        for txn in card.get("transactions") or []:
            if isinstance(txn, dict):
                row = _card_ledger_row(txn, last_four, month)
                if row is not None:
                    rows.append(row)
    return rows


_UPSERT_SQL = """
INSERT INTO ledger_transaction (
    "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב", "4 ספרות",
    fingerprint, "קטגוריה", notes, statement_month, ingested_at, excluded_from_calculations
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(fingerprint) DO UPDATE SET
    "תאריך" = excluded."תאריך",
    "בחובה" = excluded."בחובה",
    "בזכות" = excluded."בזכות",
    "מקור עסקה" = excluded."מקור עסקה",
    "פירוט נוסף" = excluded."פירוט נוסף",
    "תאור מורחב" = excluded."תאור מורחב",
    "4 ספרות" = excluded."4 ספרות",
    statement_month = COALESCE(excluded.statement_month, ledger_transaction.statement_month),
    excluded_from_calculations = excluded.excluded_from_calculations,
    "קטגוריה" = CASE
        WHEN TRIM(COALESCE(ledger_transaction."קטגוריה", '')) != '' THEN ledger_transaction."קטגוריה"
        ELSE excluded."קטגוריה"
    END,
    notes = CASE
        WHEN TRIM(COALESCE(ledger_transaction.notes, '')) != '' THEN ledger_transaction.notes
        ELSE excluded.notes
    END,
    ingested_at = ledger_transaction.ingested_at
"""


def upsert_api_rows(db_path: str, rows: list[dict[str, Any]]) -> int:
    migrate_ledger_db(db_path)
    if not rows:
        return 0
    ingested = ingested_at_for_new_ledger_row()
    tuples = [
        (
            row["date"],
            row["debit"],
            row["credit"],
            row["merchant"] or None,
            row["detail"] or None,
            row["extra"] or None,
            row["last4"] or None,
            row["fingerprint"],
            None,
            None,
            row["statement_month"],
            ingested,
            int(row["excluded"]),
        )
        for row in rows
    ]
    conn = connect_ledger(db_path)
    try:
        conn.executemany(_UPSERT_SQL, tuples)
        conn.commit()
    finally:
        conn.close()
    return len(tuples)


def iter_months(start: str, end: str) -> list[str]:
    year, month, _start, _end = parse_month(start)
    end_year, end_month, _s, _e = parse_month(end)
    if (year, month) > (end_year, end_month):
        raise BookSyncError("The start month is after the end month.")
    out: list[str] = []
    while (year, month) <= (end_year, end_month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return out


def _require_api_book(book_id: str) -> dict[str, Any]:
    spec = book_spec(book_id)
    if spec is None or not spec.get("fetcher"):
        raise BookSyncError("This book has no API fetcher.")
    return spec


def _require_token(post: PostFn) -> str:
    session = load_session()
    token = session.get("accessToken") or ""
    if not access_token_works(token, post=post):
        raise BookSyncError("Session is not ready. Complete the SMS step on this page.")
    return token


def sync_month(
    book_id: str,
    month: str,
    *,
    post: PostFn = _default_post,
    sink: Sink = _sink_drop,
) -> int:
    spec = _require_api_book(book_id)
    parse_month(month)
    token = _require_token(post)
    sample = fetch_sample(token, month=month, source="both", post=post)
    count = upsert_api_rows(str(spec["path"]), rows_from_sample(sample, month))
    sink(f"SYNC: {month} {count} rows")
    return count


def backfill(
    book_id: str,
    start: str,
    end: str,
    *,
    post: PostFn = _default_post,
    sink: Sink = _sink_drop,
) -> int:
    spec = book_spec(book_id)
    if spec is None or not spec.get("backfill"):
        raise BookSyncError("This book has no API backfill.")
    months = iter_months(start, end)
    token = _require_token(post)
    _year, _mon, start_day, _end_day = parse_month(start)
    data = graphql(token, GET_CUSTOMER, {}, post=post)
    portfolios = portfolios_from_customer(data)
    selected = select_account(portfolios, None)
    if selected is None:
        raise BookSyncError("No account was returned.")
    portfolio, account = selected
    movements = fetch_movements(
        token,
        portfolio.get("portfolioId"),
        account.get("accountId"),
        month=None,
        stop_before=start_day,
        max_pages=BACKFILL_MAX_PAGES,
        post=post,
    )
    cards, err = load_cards(
        token,
        [portfolio],
        month=None,
        with_activity=False,
        post=post,
    )
    if err and not cards:
        raise BookSyncError(err)
    total = 0
    db_path = str(spec["path"])
    for month in months:
        year, mon, first, last = parse_month(month)
        rows: list[dict[str, Any]] = []
        for movement in movements:
            if isinstance(movement, dict) and in_month(movement, first, last):
                row = _movement_ledger_row(movement)
                if row is not None:
                    rows.append(row)
        for card in cards:
            if not isinstance(card, dict) or card.get("cardStatus") != "ACTIVE":
                continue
            activity = fetch_card_month(token, card, year, mon, post=post)
            last_four = str(card.get("lastFourDigits") or "")
            for txn in activity.get("transactions") or []:
                if isinstance(txn, dict):
                    row = _card_ledger_row(txn, last_four, month)
                    if row is not None:
                        rows.append(row)
        count = upsert_api_rows(db_path, rows)
        total += count
        sink(f"BACKFILL: {month} {count} rows")
    return total


def cli_backfill(options: PullOptions, *, post: PostFn = _default_post) -> None:
    interactive_access(options=options, post=post)
    backfill(
        "shared",
        str(options.month_from),
        str(options.month_to),
        post=post,
        sink=print,
    )


_CLEARABLE = ("email", "phone", "otpToken", "accessToken", "smsChallenge")


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not local:
        return ""
    shown = local[0] + "…" if len(local) > 1 else local
    return f"{shown}@{domain}" if domain else shown


def _mask_phone(phone: str) -> str:
    if len(phone) <= 4:
        return phone
    return f"{phone[:4]}…{phone[-2:]}"


def _field(field_id: str, *, stored: bool, valid: bool, summary: str, source: str = "file") -> dict[str, Any]:
    return {"id": field_id, "stored": stored, "valid": valid, "summary": summary, "source": source}


def session_status(
    book_id: str,
    *,
    post: PostFn = _default_post,
    known_access: bool | None = None,
    prefer_sms: bool = False,
) -> dict[str, Any]:
    """Report each saved login item. This call does not send an SMS."""
    _require_api_book(book_id)
    session = load_session()
    stored_email = session.get("email") or ""
    env_email = os.environ.get("ONEZERO_EMAIL", "").strip()
    email = stored_email or env_email
    phone = session.get("phone") or ""
    otp = session.get("otpToken") or ""
    access = session.get("accessToken") or ""

    if email:
        email_field = _field(
            "email",
            stored=bool(stored_email),
            valid=True,
            summary=_mask_email(email),
            source="env" if env_email and not stored_email else "file",
        )
    else:
        email_field = _field("email", stored=False, valid=False, summary="missing", source="none")

    if not phone:
        phone_field = _field("phone", stored=False, valid=False, summary="missing", source="none")
    elif phone.startswith("+"):
        phone_field = _field("phone", stored=True, valid=True, summary=_mask_phone(phone))
    else:
        phone_field = _field("phone", stored=True, valid=False, summary="invalid")

    if not otp:
        otp_field = _field("otpToken", stored=False, valid=False, summary="missing", source="none")
    elif otp_expired(otp):
        otp_field = _field("otpToken", stored=True, valid=False, summary="expired")
    else:
        otp_field = _field("otpToken", stored=True, valid=True, summary="saved")

    if not access:
        access_valid = False
        access_field = _field("accessToken", stored=False, valid=False, summary="missing", source="none")
    elif known_access is True:
        access_valid = True
        access_field = _field("accessToken", stored=True, valid=True, summary="valid")
    elif known_access is False:
        access_valid = False
        access_field = _field("accessToken", stored=True, valid=False, summary="rejected")
    else:
        access_valid = access_token_works(access, post=post)
        access_field = _field(
            "accessToken",
            stored=True,
            valid=access_valid,
            summary="valid" if access_valid else "rejected",
        )

    with _pending_lock:
        pending = _pending.get(book_id)
    sms_waiting = False
    if not pending:
        sms_field = _field("smsChallenge", stored=False, valid=False, summary="none", source="memory")
    else:
        age = time.time() - float(pending["created"])
        if age > _CHALLENGE_SECONDS:
            sms_field = _field("smsChallenge", stored=True, valid=False, summary="expired", source="memory")
        else:
            sms_waiting = True
            sms_field = _field("smsChallenge", stored=True, valid=True, summary="waiting", source="memory")

    if prefer_sms and sms_waiting:
        state = "sms"
    elif access_valid:
        state = "ready"
    elif not email_field["valid"]:
        state = "email"
    elif not phone_field["valid"]:
        state = "phone"
    elif sms_waiting:
        state = "sms"
    elif otp_field["valid"]:
        state = "password"
    else:
        state = "login"
    return {
        "ok": True,
        "state": state,
        "fields": [email_field, phone_field, otp_field, access_field, sms_field],
    }


def clear_session_field(book_id: str, field: str, *, post: PostFn = _default_post) -> dict[str, Any]:
    """Remove one saved login item. The other items stay on disk."""
    _require_api_book(book_id)
    if field not in _CLEARABLE:
        raise BookSyncError("Unknown session field.")
    if field == "smsChallenge":
        with _pending_lock:
            _pending.pop(book_id, None)
    else:
        session = load_session()
        session.pop(field, None)
        save_session(session)
        if field == "phone":
            with _pending_lock:
                _pending.pop(book_id, None)
    return session_status(book_id, post=post)


def _bank_error(exc: ProbeError) -> str:
    text = str(exc)
    if "Max send attempts" in text:
        return (
            "The bank reached the SMS send limit. Wait before the next request. "
            "Another click now extends the wait."
        )
    if "ErrorOtpService" in text or text.startswith("HTTP 503"):
        return "The bank did not send the SMS. Wait a minute, then click Continue once."
    if "401" in text:
        return "The bank rejected the email, the password, or the saved SMS login."
    return text or "The bank rejected the request."


def prepare_session(
    book_id: str,
    *,
    email: str | None = None,
    phone: str | None = None,
    force_sms: bool = False,
    post: PostFn = _default_post,
) -> dict[str, Any]:
    _require_api_book(book_id)
    session = load_session()
    if email and email.strip():
        session["email"] = email.strip()
        save_session(session)
    if phone and phone.strip():
        session["phone"] = phone.strip()
        save_session(session)
    if force_sms:
        session = load_session()
        if not str(session.get("phone") or "").startswith("+"):
            raise BookSyncError("Phone must start with + and a country code.")
        with _pending_lock:
            pending = _pending.get(book_id)
        if pending and time.time() - float(pending["created"]) <= _CHALLENGE_SECONDS:
            raise BookSyncError("An SMS code is already waiting. Enter that code.")
        try:
            context = prepare_sms(session, post=post)
        except ProbeError as exc:
            raise BookSyncError(_bank_error(exc)) from exc
        with _pending_lock:
            _pending[book_id] = {"otp_context": context, "created": time.time()}
        return session_status(book_id, post=post, prefer_sms=True)
    return session_status(book_id, post=post)


def complete_session(
    book_id: str,
    *,
    password: str,
    sms_code: str | None = None,
    post: PostFn = _default_post,
) -> dict[str, Any]:
    _require_api_book(book_id)
    if not password:
        raise BookSyncError("Password is required.")
    session = load_session()
    if sms_code:
        with _pending_lock:
            pending = _pending.get(book_id)
        if not pending:
            raise BookSyncError("No SMS is waiting. Request a code again.")
        if time.time() - float(pending["created"]) > _CHALLENGE_SECONDS:
            with _pending_lock:
                _pending.pop(book_id, None)
            raise BookSyncError("The SMS challenge expired.")
        try:
            verify_sms(session, str(pending["otp_context"]), sms_code, post=post)
        except ProbeError as exc:
            raise BookSyncError(_bank_error(exc)) from exc
        with _pending_lock:
            _pending.pop(book_id, None)
        session = load_session()
    otp = session.get("otpToken") or ""
    if not otp or otp_expired(otp):
        raise BookSyncError("SMS token is missing.")
    email = session.get("email") or os.environ.get("ONEZERO_EMAIL", "").strip()
    if not email:
        raise BookSyncError("Email is missing from the saved session.")
    try:
        access = login(email, password, otp, post=post)
    except ProbeError as exc:
        if "401" in str(exc):
            session.pop("otpToken", None)
            session.pop("accessToken", None)
            save_session(session)
            raise BookSyncError(
                "The bank rejected that login. Click Send SMS, then enter the new code and your password."
            ) from exc
        raise BookSyncError(_bank_error(exc)) from exc
    session["accessToken"] = access
    save_session(session)
    return session_status(book_id, post=post, known_access=True)
