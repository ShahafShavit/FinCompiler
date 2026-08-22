"""Tests for physical cash wallet (ledger.cash + /api/cash/*)."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient


class CashWalletLogicTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def _open(self) -> tuple[object, sqlite3.Connection, str]:
        import config as config_mod

        tmp = tempfile.TemporaryDirectory()
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        from ledger import migrate_ledger_db
        from ledger.cash import (
            balance_agorot,
            delete_event,
            insert_count,
            insert_spend_or_receive,
            list_events_for_month,
        )

        migrate_ledger_db()
        conn = sqlite3.connect(config_mod.ledger_db_file)
        conn.execute("PRAGMA foreign_keys = ON")
        # stash helpers on self for tests
        self._tmp = tmp
        self._helpers = {
            "balance_agorot": balance_agorot,
            "delete_event": delete_event,
            "insert_count": insert_count,
            "insert_spend_or_receive": insert_spend_or_receive,
            "list_events_for_month": list_events_for_month,
        }
        return tmp, conn, config_mod.ledger_db_file

    def test_spend_receive_count_and_hole(self) -> None:
        from ledger.cash import CashError

        _tmp, conn, _path = self._open()
        try:
            h = self._helpers
            # Seed via first count: expected=0, counted=350.50 ₪
            seed = h["insert_count"](
                conn,
                event_date="2026-08-01",
                denominations={"20000": 1, "10000": 1, "5000": 1, "50": 1},
            )
            self.assertEqual(seed["kind"], "count")
            self.assertEqual(seed["expected_agorot"], 0)
            self.assertEqual(seed["counted_agorot"], 35050)
            self.assertEqual(seed["delta_agorot"], 35050)
            self.assertEqual(h["balance_agorot"](conn), 35050)

            recv = h["insert_spend_or_receive"](
                conn,
                kind="receive",
                event_date="2026-08-05",
                amount_agorot=20000,
                note="ATM",
            )
            self.assertEqual(recv["delta_agorot"], 20000)
            self.assertEqual(h["balance_agorot"](conn), 55050)

            spend = h["insert_spend_or_receive"](
                conn,
                kind="spend",
                event_date="2026-08-10",
                amount_agorot=1230,  # ₪12.30
            )
            self.assertEqual(spend["delta_agorot"], -1230)
            self.assertEqual(h["balance_agorot"](conn), 53820)

            # Count mismatch: pocket has ₪500 → hole = 50000 - 53820 = -3820
            count = h["insert_count"](
                conn,
                event_date="2026-08-15",
                denominations={"20000": 2, "10000": 1},
            )
            self.assertEqual(count["expected_agorot"], 53820)
            self.assertEqual(count["counted_agorot"], 50000)
            self.assertEqual(count["delta_agorot"], -3820)
            self.assertEqual(h["balance_agorot"](conn), 50000)

            month = h["list_events_for_month"](conn, "2026-08")
            self.assertEqual(len(month), 4)
            self.assertEqual(month[-1]["balance_after_agorot"], 50000)

            # Reject non-multiple of 10 agorot
            with self.assertRaises(CashError) as cm:
                h["insert_spend_or_receive"](
                    conn,
                    kind="spend",
                    event_date="2026-08-16",
                    amount_agorot=15,
                )
            self.assertEqual(cm.exception.code, "invalid_agorot")

            # Delete last count → balance back to pre-count
            self.assertTrue(h["delete_event"](conn, count["id"]))
            self.assertEqual(h["balance_agorot"](conn), 53820)
        finally:
            conn.close()
            self._tmp.cleanup()

    def test_reject_unknown_denomination(self) -> None:
        from ledger.cash import CashError

        _tmp, conn, _path = self._open()
        try:
            with self.assertRaises(CashError) as cm:
                self._helpers["insert_count"](
                    conn,
                    event_date="2026-08-01",
                    denominations={"25": 1},
                )
            self.assertEqual(cm.exception.code, "invalid_denominations")
        finally:
            conn.close()
            self._tmp.cleanup()

    def test_excel_backfill_and_category(self) -> None:
        from ledger.cash import apply_backfill, balance_agorot, patch_event

        def _tsv(date: str, dens: list[int | None], cat: str = "") -> str:
            cells = [date] + ["" if v is None else str(v) for v in dens]
            if cat:
                cells.append(cat)
            return "\t".join(cells)

        _tmp, conn, _path = self._open()
        try:
            paste = "\n".join(
                [
                    _tsv("01/01/2024", [118, 15, 27, 5, 7, 5, 1, 1, 2, 0], "פתיחה"),
                    _tsv("01/03/2024", [None, None, None, None, 2, 1, None, -1, -1, None], "משיכה"),
                    _tsv("23/03/2024", [None, None, None, None, None, None, 1, None, 13, None], "כספומט"),
                    _tsv("31/03/2024", [None, None, None, None, None, None, None, None, 2, None]),
                    _tsv("06/04/2024", [None, None, None, None, None, None, -1, None, None, 1], "החלפה"),
                    _tsv("24/04/2024", [None, None, None, None, None, None, None, None, 2, None]),
                ]
            )
            out = apply_backfill(conn, paste, first_row_is_count=True)
            self.assertEqual(out["inserted"], 6)
            # Opening 411.30 + (-130) + 1320 + 200 + 180 + 200 = 2181.30
            self.assertEqual(out["balance_agorot"], 218130)
            self.assertEqual(balance_agorot(conn), 218130)
            self.assertEqual(out["events"][0]["kind"], "count")
            self.assertEqual(out["events"][0]["category"], "פתיחה")
            self.assertEqual(out["events"][1]["kind"], "spend")
            self.assertEqual(out["events"][1]["delta_agorot"], -13000)
            self.assertEqual(out["events"][1]["category"], "משיכה")
            self.assertEqual(out["events"][2]["kind"], "receive")
            self.assertEqual(out["events"][2]["delta_agorot"], 132000)

            patched = patch_event(conn, out["events"][3]["id"], category="תוספת")
            self.assertEqual(patched["category"], "תוספת")

            from ledger.cash import current_piece_inventory, inventory_split_agorot, summary

            split = inventory_split_agorot(current_piece_inventory(conn))
            # After Excel rows through 24/04/2024 the reconstructed mix equals balance
            self.assertEqual(split["pieces_agorot"], 218130)
            self.assertGreater(split["paper_agorot"], 0)
            self.assertGreater(split["coin_agorot"], 0)
            self.assertEqual(split["paper_agorot"] + split["coin_agorot"], split["pieces_agorot"])
            s = summary(conn)
            self.assertEqual(s["paper_agorot"], split["paper_agorot"])
            self.assertEqual(s["coin_agorot"], split["coin_agorot"])
        finally:
            conn.close()
            self._tmp.cleanup()


class CashApiSmokeTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_api_post_get_delete(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from api.main import create_app
            from ledger import migrate_ledger_db

            migrate_ledger_db()

            with TestClient(create_app()) as client:
                empty = client.get("/api/cash/summary?month=2026-08")
                self.assertEqual(empty.status_code, 200)
                ej = empty.json()
                self.assertTrue(ej.get("ok"))
                self.assertEqual(ej.get("balance_agorot"), 0)

                created = client.post(
                    "/api/cash/events",
                    content=json.dumps(
                        {
                            "kind": "count",
                            "event_date": "2026-08-01",
                            "denominations": {"10000": 1, "50": 2},
                        }
                    ),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(created.status_code, 200, msg=created.text)
                cj = created.json()
                self.assertTrue(cj.get("ok"))
                self.assertEqual(cj.get("balance_agorot"), 10100)
                eid = cj["event"]["id"]

                spend = client.post(
                    "/api/cash/events",
                    json={
                        "kind": "spend",
                        "event_date": "2026-08-02",
                        "amount_agorot": 1000,
                        "note": "coffee",
                    },
                )
                self.assertEqual(spend.status_code, 200)
                self.assertEqual(spend.json().get("balance_agorot"), 9100)

                bad = client.post(
                    "/api/cash/events",
                    json={
                        "kind": "spend",
                        "event_date": "2026-08-03",
                        "amount_agorot": 5,
                    },
                )
                self.assertEqual(bad.status_code, 400)
                self.assertEqual(bad.json().get("error"), "invalid_agorot")

                events = client.get("/api/cash/events?month=2026-08")
                self.assertEqual(events.status_code, 200)
                evj = events.json()
                self.assertTrue(evj.get("ok"))
                self.assertEqual(len(evj.get("events") or []), 2)
                self.assertEqual(evj["events"][-1]["balance_after_agorot"], 9100)

                deleted = client.delete(f"/api/cash/events/{eid}")
                self.assertEqual(deleted.status_code, 200)
                # After deleting seed count, only spend remains → balance -1000
                self.assertEqual(deleted.json().get("balance_agorot"), -1000)

                summary = client.get("/api/cash/summary")
                self.assertEqual(summary.status_code, 200)
                self.assertEqual(summary.json().get("balance_agorot"), -1000)

                # Wipe remaining spend then backfill sample
                left = client.get("/api/cash/events?month=2026-08").json()["events"]
                for ev in left:
                    client.delete(f"/api/cash/events/{ev['id']}")

                bf = client.post(
                    "/api/cash/backfill",
                    json={
                        "first_row_is_count": True,
                        "text": "\n".join(
                            [
                                "\t".join(
                                    ["01/01/2024", "118", "15", "27", "5", "7", "5", "1", "1", "2", "0", "פתיחה"]
                                ),
                                "\t".join(
                                    [
                                        "01/03/2024",
                                        "",
                                        "",
                                        "",
                                        "",
                                        "2",
                                        "1",
                                        "",
                                        "-1",
                                        "-1",
                                        "",
                                        "משיכה",
                                    ]
                                ),
                            ]
                        ),
                    },
                )
                self.assertEqual(bf.status_code, 200, msg=bf.text)
                self.assertEqual(bf.json().get("inserted"), 2)
                self.assertEqual(bf.json().get("balance_agorot"), 28130)

                eid2 = bf.json()["events"][1]["id"]
                patched = client.patch(
                    f"/api/cash/events/{eid2}",
                    json={"category": "ATM"},
                )
                self.assertEqual(patched.status_code, 200)
                self.assertEqual(patched.json()["event"]["category"], "ATM")

                all_ev = client.get("/api/cash/events?month=all")
                self.assertEqual(all_ev.status_code, 200)
                self.assertEqual(all_ev.json().get("month"), "all")
                self.assertEqual(len(all_ev.json().get("events") or []), 2)
                self.assertEqual(all_ev.json().get("last_activity_month"), "2024-03")

                sum_default = client.get("/api/cash/summary")
                self.assertEqual(sum_default.status_code, 200)
                self.assertEqual(sum_default.json().get("last_activity_month"), "2024-03")
                self.assertEqual(sum_default.json().get("month"), "2024-03")

                amt = client.patch(
                    f"/api/cash/events/{eid2}",
                    json={"amount_agorot": 5000, "event_date": "2024-03-02"},
                )
                self.assertEqual(amt.status_code, 200, msg=amt.text)
                self.assertEqual(amt.json()["event"]["delta_agorot"], -5000)
                self.assertEqual(amt.json()["event"]["event_date"], "2024-03-02")


if __name__ == "__main__":
    unittest.main()
