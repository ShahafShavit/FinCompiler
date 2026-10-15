"""Controls for the OneZero probe: month, account, and card activity."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import unittest


def _load_probe():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "tools", "onezero_probe.py")
    spec = importlib.util.spec_from_file_location("onezero_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["onezero_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _movement(day: str, movement_id: str, timestamp: str | None = None) -> dict:
    return {
        "movementId": movement_id,
        "valueDate": day,
        "movementTimestamp": timestamp or f"{day}T12:00:00Z",
        "movementAmount": "10.00",
        "movementCurrency": "ILS",
        "movementType": "TRANSFER",
        "creditDebit": "DEBIT",
        "description": "shop",
    }


def _customer() -> dict:
    return {
        "customer": {
            "portfolios": [
                {
                    "portfolioId": "p1",
                    "portfolioNum": "100",
                    "accounts": [
                        {
                            "accountId": "a1",
                            "accountType": "CHECKING",
                            "subType": None,
                            "currency": "ILS",
                            "status": "ACTIVE",
                        },
                        {
                            "accountId": "a2",
                            "accountType": "SAVINGS",
                            "subType": "DEPOSIT",
                            "currency": "ILS",
                            "status": "ACTIVE",
                        },
                    ],
                }
            ]
        }
    }


def _cards() -> dict:
    return {
        "cardsDetailsV3": {
            "cardsDetails": [
                {
                    "baseDetails": {
                        "cardId": "c1",
                        "lastFourDigits": "1234",
                        "cardType": "CREDIT",
                        "cardStatus": "ACTIVE",
                        "currency": "ILS",
                        "provider": "ISRACARD",
                    }
                },
                {
                    "baseDetails": {
                        "cardId": "c2",
                        "lastFourDigits": "9876",
                        "cardType": "CREDIT",
                        "cardStatus": "FROZEN",
                        "currency": "ILS",
                        "provider": "ISRACARD",
                    }
                },
            ]
        }
    }


def _activity() -> dict:
    return {
        "cardActivityV3": {
            "transactions": [
                {
                    "transactionId": "t1",
                    "merchantName": "Shop",
                    "purchaseDate": "2026-09-03",
                    "paymentDate": "2026-10-02",
                    "debitAmount": {"amount": 12.5, "currency": "ILS"},
                    "originalAmount": {"amount": 12.5, "currency": "ILS"},
                    "direction": "DEBIT",
                    "status": "BOOKED",
                    "type": "REGULAR",
                    "numberOfPayments": 1,
                    "paymentNumber": 1,
                }
            ]
        }
    }


class OneZeroProbeTests(unittest.TestCase):
    def test_parse_month_uses_calendar_length(self) -> None:
        self.assertEqual(probe.parse_month("2026-09")[2:], ("2026-09-01", "2026-09-30"))
        self.assertEqual(probe.parse_month("2024-02")[2:], ("2024-02-01", "2024-02-29"))
        with self.assertRaises(probe.ProbeError):
            probe.parse_month("2026-13")

    def test_default_pull_is_one_page_of_the_first_account(self) -> None:
        calls: list[dict] = []

        def post(url: str, payload: dict, headers: dict | None):
            query = payload["query"]
            calls.append(payload["variables"])
            if "GetCustomer" in query:
                return 200, {"data": _customer()}
            if "GetMovements" in query:
                return 200, {
                    "data": {
                        "movements": {
                            "movements": [_movement("2026-10-08", "m1")],
                            "pagination": {"cursor": "next", "hasMore": True},
                        }
                    }
                }
            raise AssertionError(query)

        sample = probe.fetch_sample("token", post=post)
        self.assertEqual(sample["accountId"], "a1")
        self.assertEqual(sample["source"], "checking")
        self.assertIsNone(sample["month"])
        self.assertEqual(sample["movementCount"], 1)
        self.assertNotIn("cards", sample)
        movement_calls = [item for item in calls if "pagination" in item]
        self.assertEqual(len(movement_calls), 1)
        self.assertEqual(movement_calls[0]["accountId"], "a1")
        self.assertIsNone(movement_calls[0]["pagination"]["cursor"])

    def test_month_keeps_value_dates_inside_the_month(self) -> None:
        pages = {
            None: {
                "movements": [
                    _movement("2026-10-08", "m1"),
                    _movement("2026-10-01", "m2", "2026-09-30T23:00:00Z"),
                    _movement("2026-09-17", "m3"),
                ],
                "pagination": {"cursor": "c2", "hasMore": True},
            },
            "c2": {
                "movements": [
                    _movement("2026-09-02", "m4"),
                    _movement("2026-08-20", "m5"),
                ],
                "pagination": {"cursor": "c3", "hasMore": True},
            },
        }
        cursors: list[str | None] = []

        def post(url: str, payload: dict, headers: dict | None):
            query = payload["query"]
            if "GetCustomer" in query:
                return 200, {"data": _customer()}
            if "GetMovements" in query:
                cursor = payload["variables"]["pagination"]["cursor"]
                cursors.append(cursor)
                return 200, {"data": {"movements": pages[cursor]}}
            raise AssertionError(query)

        sample = probe.fetch_sample("token", month="2026-09", post=post)
        self.assertEqual(cursors, [None, "c2"])
        self.assertEqual([row["movementId"] for row in sample["movements"]], ["m3", "m4"])
        self.assertEqual(sample["movementCount"], 2)

    def test_account_id_selects_that_account(self) -> None:
        seen: list[str] = []

        def post(url: str, payload: dict, headers: dict | None):
            query = payload["query"]
            if "GetCustomer" in query:
                return 200, {"data": _customer()}
            if "GetMovements" in query:
                seen.append(payload["variables"]["accountId"])
                return 200, {
                    "data": {
                        "movements": {
                            "movements": [_movement("2026-09-02", "m4")],
                            "pagination": {"cursor": None, "hasMore": False},
                        }
                    }
                }
            raise AssertionError(query)

        sample = probe.fetch_sample("token", account_id="a2", post=post)
        self.assertEqual(seen, ["a2"])
        self.assertEqual(sample["accountId"], "a2")
        types = {
            account["accountId"]: account["accountType"]
            for account in sample["portfolios"][0]["accounts"]
        }
        self.assertEqual(types, {"a1": "CHECKING", "a2": "SAVINGS"})

    def test_unknown_account_id_fails(self) -> None:
        def post(url: str, payload: dict, headers: dict | None):
            return 200, {"data": _customer()}

        with self.assertRaises(probe.ProbeError):
            probe.fetch_sample("token", account_id="missing", post=post)

    def test_card_month_reads_active_cards_only(self) -> None:
        activity_vars: list[dict] = []

        def post(url: str, payload: dict, headers: dict | None):
            query = payload["query"]
            if "GetCustomer" in query:
                return 200, {"data": _customer()}
            if "GetCardsDetails" in query:
                self.assertEqual(payload["variables"]["portfolioId"], "p1")
                return 200, {"data": _cards()}
            if "GetCardActivity" in query:
                activity_vars.append(payload["variables"])
                return 200, {"data": _activity()}
            raise AssertionError("movements are not part of a card pull")

        sample = probe.fetch_sample("token", month="2026-09", source="cards", post=post)
        self.assertEqual(activity_vars, [{"cardId": "c1", "billingYear": 2026, "billingMonth": 9}])
        self.assertNotIn("movements", sample)
        frozen = sample["cards"][1]
        self.assertEqual(frozen["transactionCount"], 0)
        self.assertEqual(sample["cards"][0]["transactions"][0]["debitAmount"], 12.5)
        self.assertEqual(sample["cards"][0]["transactions"][0]["merchantName"], "Shop")

    def test_one_card_error_keeps_the_other_card(self) -> None:
        def post(url: str, payload: dict, headers: dict | None):
            query = payload["query"]
            if "GetCustomer" in query:
                return 200, {"data": _customer()}
            if "GetCardsDetails" in query:
                data = _cards()
                data["cardsDetailsV3"]["cardsDetails"][1]["baseDetails"]["cardStatus"] = "ACTIVE"
                data["cardsDetailsV3"]["cardsDetails"][1]["baseDetails"]["cardId"] = "c-bad"
                return 200, {"data": data}
            if "GetCardActivity" in query:
                if payload["variables"]["cardId"] == "c-bad":
                    return 200, {"errors": [{"message": "card month unavailable"}]}
                return 200, {"data": _activity()}
            raise AssertionError(query)

        sample = probe.fetch_sample("token", month="2026-09", source="cards", post=post)
        self.assertEqual(sample["cards"][0]["transactionCount"], 1)
        self.assertEqual(sample["cards"][1]["error"], "card month unavailable")

    def test_card_list_failure_on_both_keeps_checking_rows(self) -> None:
        def post(url: str, payload: dict, headers: dict | None):
            query = payload["query"]
            if "GetCustomer" in query:
                return 200, {"data": _customer()}
            if "GetCardsDetails" in query:
                return 200, {"errors": [{"message": "cards unavailable"}]}
            if "GetMovements" in query:
                return 200, {
                    "data": {
                        "movements": {
                            "movements": [_movement("2026-09-02", "m4")],
                            "pagination": {"cursor": None, "hasMore": False},
                        }
                    }
                }
            raise AssertionError(query)

        sample = probe.fetch_sample("token", month="2026-09", source="both", post=post)
        self.assertEqual(sample["cardsError"], "cards unavailable")
        self.assertEqual(sample["movementCount"], 1)
        self.assertEqual(sample["cards"], [])

    def test_list_accounts_skips_movements_and_card_lines(self) -> None:
        def post(url: str, payload: dict, headers: dict | None):
            query = payload["query"]
            if "GetCustomer" in query:
                return 200, {"data": _customer()}
            if "GetCardsDetails" in query:
                return 200, {"data": _cards()}
            raise AssertionError(query)

        sample = probe.fetch_sample("token", list_accounts=True, post=post)
        self.assertEqual(sample["source"], "accounts")
        self.assertNotIn("movements", sample)
        self.assertIsNone(sample["cards"][0].get("transactionCount"))
        self.assertEqual(sample["cards"][1]["lastFourDigits"], "9876")

    def test_cards_require_a_month_flag(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                probe.options_from_args(["--source", "cards"])
        options = probe.options_from_args(["--month", "2026-09", "--source", "both", "--account-id", " a2 "])
        self.assertEqual(options.month, "2026-09")
        self.assertEqual(options.source, "both")
        self.assertEqual(options.account_id, "a2")

    def test_401_keeps_the_sms_token(self) -> None:
        self.assertEqual(
            probe._safe_message({"errorResponse": {"type": "ErrorIdTokenRevoke"}}),
            "ErrorIdTokenRevoke",
        )
        self.assertIn(
            "Max send attempts reached",
            probe._safe_message(
                {"errorResponse": {"type": "ErrorOtpService", "description": "Max send attempts reached"}}
            ),
        )
        self.assertFalse(probe.refresh_otp_after(probe.ProbeError("HTTP 401")))
        self.assertTrue(probe.refresh_otp_after(probe.ProbeError("ErrorIdTokenRevoke")))
        self.assertTrue(probe.refresh_otp_after(probe.ProbeError("HTTP 500")))
        expired = probe.base64.urlsafe_b64encode(
            probe.json.dumps({"exp": 1}).encode()
        ).decode().rstrip("=")
        header = probe.base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        self.assertTrue(probe.otp_expired(f"{header}.{expired}.sig"))
        options = probe.options_from_args(["--new-sms"])
        self.assertTrue(options.new_sms)

    def test_console_hides_token_account_ids(self) -> None:
        self.assertEqual(probe._account_label("eyJabc.def.ghi"), "account")
        self.assertEqual(probe._account_label("cash-42"), "account cash-42")
