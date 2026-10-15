"""Book switch, API sync, backfill, SMS session, and payer-by-card."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from api.main import create_app
from ledger.api_sync import (
    BookSyncError,
    _card_ledger_row,
    _movement_ledger_row,
    _pending,
    backfill,
    clear_session_field,
    complete_session,
    prepare_session,
    session_status,
    upsert_api_rows,
)
from ledger.books import active_ledger_path, ledger_path, personal_ledger_path, reset_active_book, set_active_book
from ledger.card_payer import replace_card_payers
from ledger.store import connect_ledger


def _reload_config(root: str):
    os.environ["FINANCE_WORKSPACE_ROOT"] = root
    import config as config_mod

    with patch("dotenv.load_dotenv"):
        importlib.reload(config_mod)
    return config_mod


def _movement(day: str, movement_id: str, kind: str = "TRANSFER") -> dict:
    return {
        "movementId": movement_id,
        "valueDate": day,
        "movementTimestamp": f"{day}T12:00:00Z",
        "movementAmount": "10.00",
        "movementCurrency": "ILS",
        "movementType": kind,
        "creditDebit": "DEBIT",
        "description": "shop",
    }


class BookIsolationTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)

    def test_shared_book_does_not_change_the_personal_write_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _reload_config(tmp)
            token = set_active_book("shared")
            try:
                self.assertNotEqual(active_ledger_path(), personal_ledger_path())
                self.assertTrue(personal_ledger_path().endswith("ledger.sqlite"))
                self.assertTrue(active_ledger_path().endswith(os.path.join("books", "shared.sqlite")))
            finally:
                reset_active_book(token)

    def test_search_reads_the_active_book(self) -> None:
        from api.ledger_search import search_ledger

        with tempfile.TemporaryDirectory() as tmp:
            _reload_config(tmp)
            personal = _movement_ledger_row(_movement("2026-01-02", "p1"))
            assert personal is not None
            personal["debit"] = 10
            shared = _movement_ledger_row(_movement("2026-01-03", "s1"))
            assert shared is not None
            shared["debit"] = 40
            upsert_api_rows(ledger_path("personal"), [personal])
            upsert_api_rows(ledger_path("shared"), [shared])
            token = set_active_book("personal")
            try:
                _code, payload = search_ledger({"include_excluded": ["1"]})
                self.assertEqual(payload["rows"][0]["בחובה"], 10)
            finally:
                reset_active_book(token)
            token = set_active_book("shared")
            try:
                _code, payload = search_ledger({"include_excluded": ["1"]})
                self.assertEqual(payload["rows"][0]["בחובה"], 40)
            finally:
                reset_active_book(token)

    def test_unknown_book_header_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _reload_config(tmp)
            with TestClient(create_app()) as client:
                response = client.get("/api/ledger/search", headers={"X-Finance-Book": "nope"})
            self.assertEqual(response.status_code, 400)

    def test_personal_backfill_is_refused(self) -> None:
        with self.assertRaises(BookSyncError):
            backfill("personal", "2026-01", "2026-03")


class ApiLedgerTests(unittest.TestCase):
    def tearDown(self) -> None:
        _pending.clear()
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)

    def test_second_sync_keeps_category_and_updates_bank_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _reload_config(tmp)
            row = _movement_ledger_row(_movement("2026-04-01", "m-keep"))
            assert row is not None
            path = ledger_path("shared")
            upsert_api_rows(path, [row])
            conn = connect_ledger(path)
            try:
                conn.execute(
                    'UPDATE ledger_transaction SET "קטגוריה" = ? WHERE fingerprint = ?',
                    ("food", row["fingerprint"]),
                )
                conn.commit()
            finally:
                conn.close()
            row["merchant"] = "updated shop"
            row["debit"] = 12
            upsert_api_rows(path, [row])
            conn = connect_ledger(path)
            try:
                stored = conn.execute(
                    'SELECT "מקור עסקה", "בחובה", "קטגוריה" FROM ledger_transaction'
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(stored, [("updated shop", 12, "food")])

    def test_card_settlement_is_excluded_and_payer_follows_the_card(self) -> None:
        from api.ledger_search import search_ledger

        with tempfile.TemporaryDirectory() as tmp:
            _reload_config(tmp)
            settlement = _movement_ledger_row(_movement("2026-05-01", "settle", "CARD_SETTLEMENT"))
            card = _card_ledger_row(
                {
                    "transactionId": "card-1",
                    "merchantName": "cafe",
                    "purchaseDate": "2026-05-02",
                    "debitAmount": 8,
                    "direction": "DEBIT",
                },
                "1234",
                "2026-05",
            )
            assert settlement is not None and card is not None
            path = ledger_path("shared")
            upsert_api_rows(path, [settlement, card])
            replace_card_payers(path, [{"last_four": "1234", "payer": "Ada"}])
            token = set_active_book("shared")
            try:
                _code, payload = search_ledger({"include_excluded": ["1"]})
            finally:
                reset_active_book(token)
            by_merchant = {row["מקור עסקה"]: row for row in payload["rows"]}
            self.assertEqual(by_merchant["shop"]["excluded_from_calculations"], 1)
            self.assertIsNone(by_merchant["shop"]["payer"])
            self.assertEqual(by_merchant["cafe"]["payer"], "Ada")
            self.assertEqual(by_merchant["cafe"]["excluded_from_calculations"], 0)
            replace_card_payers(path, [{"last_four": "1234", "payer": "Bea"}])
            token = set_active_book("shared")
            try:
                _code, payload = search_ledger({"include_excluded": ["1"]})
                cafe = next(row for row in payload["rows"] if row["מקור עסקה"] == "cafe")
                self.assertEqual(cafe["payer"], "Bea")
                from api.dashboard import payer_totals

                totals = payer_totals()
                self.assertEqual(totals["rows"], [{"payer": "Bea", "expense": 8.0, "income": 0.0, "count": 1}])
            finally:
                reset_active_book(token)

    def test_backfill_upserts_each_month_once_and_keeps_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_mod = _reload_config(tmp)
            card_calls: list[int] = []

            def post(url: str, payload: dict, headers: dict | None):
                query = payload.get("query") or ""
                if "GetCustomer" in query:
                    return 200, {
                        "data": {
                            "customer": {
                                "portfolios": [
                                    {
                                        "portfolioId": "p1",
                                        "portfolioNum": "100",
                                        "accounts": [
                                            {
                                                "accountId": "a1",
                                                "accountType": "CHECKING",
                                                "currency": "ILS",
                                                "status": "ACTIVE",
                                            }
                                        ],
                                    }
                                ]
                            }
                        }
                    }
                if "GetMovements" in query:
                    return 200, {
                        "data": {
                            "movements": {
                                "movements": [_movement("2026-08-02", "chk-aug")],
                                "pagination": {"cursor": None, "hasMore": False},
                            }
                        }
                    }
                if "GetCardsDetails" in query:
                    return 200, {
                        "data": {
                            "cardsDetailsV3": {
                                "cardsDetails": [
                                    {
                                        "baseDetails": {
                                            "cardId": "c1",
                                            "lastFourDigits": "1234",
                                            "cardStatus": "ACTIVE",
                                            "currency": "ILS",
                                        }
                                    }
                                ]
                            }
                        }
                    }
                if "GetCardActivity" in query:
                    month = int(payload["variables"]["billingMonth"])
                    card_calls.append(month)
                    return 200, {
                        "data": {
                            "cardActivityV3": {
                                "transactions": [
                                    {
                                        "transactionId": f"tx-{month}",
                                        "merchantName": "cafe",
                                        "purchaseDate": f"2026-{month:02d}-03",
                                        "debitAmount": {"amount": 5, "currency": "ILS"},
                                        "direction": "DEBIT",
                                        "status": "BOOKED",
                                        "type": "STATEMENT_CHARGE",
                                    }
                                ]
                            }
                        }
                    }
                raise AssertionError(query or url)

            session_path = os.path.join(config_mod.private_dir, "onezero-probe", "session.json")
            os.makedirs(os.path.dirname(session_path), exist_ok=True)
            with open(session_path, "w", encoding="utf-8") as handle:
                json.dump({"accessToken": "tok", "email": "a@b.c", "phone": "+972500000000"}, handle)

            backfill("shared", "2026-08", "2026-10", post=post, sink=lambda _msg: None)
            self.assertEqual(card_calls, [8, 9, 10])
            path = ledger_path("shared")
            conn = connect_ledger(path)
            try:
                count = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
                conn.execute('UPDATE ledger_transaction SET "קטגוריה" = ? WHERE "4 ספרות" = ?', ("food", "1234"))
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(count, 4)
            backfill("shared", "2026-08", "2026-10", post=post, sink=lambda _msg: None)
            conn = connect_ledger(path)
            try:
                categories = conn.execute('SELECT "קטגוריה" FROM ledger_transaction WHERE "4 ספרות" = ?', ("1234",)).fetchall()
                count_again = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count_again, 4)
            self.assertEqual({row[0] for row in categories}, {"food"})

    def test_sms_complete_does_not_store_the_password_or_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_mod = _reload_config(tmp)
            secret = "secret-pass"
            code = "654321"

            prepared = {"n": 0}

            def post(url: str, payload: dict, headers: dict | None):
                if url.endswith("/devices/token"):
                    return 200, {"resultData": {"deviceToken": "dev"}}
                if url.endswith("/otp/prepare"):
                    prepared["n"] += 1
                    return 200, {"resultData": {"otpContext": "ctx"}}
                if url.endswith("/otp/verify"):
                    self.assertEqual(payload["otpCode"], code)
                    return 200, {"resultData": {"otpToken": _future_otp()}}
                if url.endswith("/getIdToken"):
                    self.assertEqual(payload["pass"], secret)
                    return 200, {"resultData": {"idToken": "id-token"}}
                if url.endswith("/sessions/token"):
                    return 200, {"resultData": {"accessToken": "access-token"}}
                if "graphql" in url:
                    return 200, {"data": {"customer": {"portfolios": []}}}
                raise AssertionError(url)

            state = prepare_session("shared", email="a@b.c", phone="+972500000000", force_sms=True, post=post)
            self.assertEqual(state["state"], "sms")
            self.assertEqual(prepared["n"], 1)
            with self.assertRaises(BookSyncError) as waiting:
                prepare_session("shared", force_sms=True, post=post)
            self.assertIn("already waiting", str(waiting.exception))
            self.assertEqual(prepared["n"], 1)
            done = complete_session("shared", password=secret, sms_code=code, post=post)
            self.assertEqual(done["state"], "ready")
            session_path = os.path.join(config_mod.private_dir, "onezero-probe", "session.json")
            with open(session_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn(secret, text)
            self.assertNotIn(code, text)
            saved = json.loads(text)
            self.assertEqual(saved["accessToken"], "access-token")
            self.assertNotIn("password", saved)

    def test_saved_otp_stays_on_password_until_a_new_sms_is_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_mod = _reload_config(tmp)
            session_path = os.path.join(config_mod.private_dir, "onezero-probe", "session.json")
            os.makedirs(os.path.dirname(session_path), exist_ok=True)
            with open(session_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "email": "a@b.c",
                        "phone": "+972500000000",
                        "otpToken": _future_otp(),
                        "accessToken": "dead",
                    },
                    handle,
                )
            prepared = {"count": 0}

            def post(url: str, payload: dict, headers: dict | None):
                if "graphql" in url:
                    return 401, {"message": "unauthorized"}
                if url.endswith("/otp/prepare"):
                    prepared["count"] += 1
                    return 200, {"resultData": {"otpContext": "ctx"}}
                if url.endswith("/devices/token"):
                    return 200, {"resultData": {"deviceToken": "dev"}}
                raise AssertionError(url)

            held = prepare_session("shared", post=post)
            self.assertEqual(held["state"], "password")
            self.assertEqual(prepared["count"], 0)
            sent = prepare_session("shared", force_sms=True, post=post)
            self.assertEqual(sent["state"], "sms")
            self.assertEqual(prepared["count"], 1)

    def test_otp_service_failure_is_a_session_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_mod = _reload_config(tmp)
            session_path = os.path.join(config_mod.private_dir, "onezero-probe", "session.json")
            os.makedirs(os.path.dirname(session_path), exist_ok=True)
            with open(session_path, "w", encoding="utf-8") as handle:
                json.dump({"email": "a@b.c", "phone": "+972500000000"}, handle)

            def post(url: str, payload: dict, headers: dict | None):
                if "graphql" in url:
                    return 401, {"message": "unauthorized"}
                if url.endswith("/otp/prepare"):
                    return 503, {"errorResponse": {"type": "ErrorOtpService"}}
                if url.endswith("/devices/token"):
                    return 200, {"resultData": {"deviceToken": "dev"}}
                raise AssertionError(url)

            with self.assertRaises(BookSyncError) as caught:
                prepare_session("shared", force_sms=True, post=post)
            self.assertIn("did not send the SMS", str(caught.exception))

    def test_status_shows_each_item_and_clear_removes_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_mod = _reload_config(tmp)
            otp = _future_otp(1)
            session_path = os.path.join(config_mod.private_dir, "onezero-probe", "session.json")
            os.makedirs(os.path.dirname(session_path), exist_ok=True)
            with open(session_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "email": "ada@bank.test",
                        "phone": "+972500000000",
                        "otpToken": otp,
                        "accessToken": "dead",
                    },
                    handle,
                )

            def post(url: str, payload: dict, headers: dict | None):
                if "graphql" in url:
                    return 401, {"message": "unauthorized"}
                raise AssertionError(url)

            status = session_status("shared", post=post)
            self.assertEqual(status["state"], "login")
            by_id = {row["id"]: row for row in status["fields"]}
            self.assertTrue(by_id["email"]["valid"])
            self.assertEqual(by_id["email"]["summary"], "a…@bank.test")
            self.assertTrue(by_id["phone"]["valid"])
            self.assertFalse(by_id["otpToken"]["valid"])
            self.assertEqual(by_id["otpToken"]["summary"], "expired")
            self.assertFalse(by_id["accessToken"]["valid"])
            self.assertEqual(by_id["accessToken"]["summary"], "rejected")
            self.assertEqual(by_id["smsChallenge"]["summary"], "none")
            held = prepare_session("shared", post=post)
            self.assertEqual(held["state"], "login")

            cleared = clear_session_field("shared", "accessToken", post=post)
            with open(session_path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["email"], "ada@bank.test")
            self.assertEqual(saved["phone"], "+972500000000")
            self.assertEqual(saved["otpToken"], otp)
            self.assertEqual(saved["accessToken"], "")
            again = {row["id"]: row for row in cleared["fields"]}
            self.assertFalse(again["accessToken"]["stored"])
            self.assertTrue(again["otpToken"]["stored"])
            self.assertTrue(again["email"]["stored"])
            with self.assertRaises(BookSyncError):
                clear_session_field("shared", "password", post=post)


def _future_otp(exp: int = 9999999999) -> str:
    import base64

    def part(obj: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
        return raw

    return f"{part({'alg': 'none'})}.{part({'exp': exp})}.sig"


if __name__ == "__main__":
    unittest.main()
