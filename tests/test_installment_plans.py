"""Tests for installments + recurring fees API."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import config as config_mod


class InstallmentPlansTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        importlib.reload(config_mod)

    def _fresh_db(self) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["FINANCE_WORKSPACE_ROOT"] = tmp.name
        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        from ledger import migrate_ledger_db

        migrate_ledger_db()
        return config_mod.ledger_db_file

    def _insert(
        self,
        db: str,
        *,
        date: str,
        debit: float = 0,
        credit: float = 0,
        source: str = "",
        detail: str = "",
        extended: str = "",
        category: str = "",
        fingerprint: str,
        statement_month: str | None = None,
        card: str = "",
        excluded: int = 0,
    ) -> int:
        conn = sqlite3.connect(db)
        try:
            cur = conn.execute(
                """
                INSERT INTO ledger_transaction (
                  "תאריך", "בחובה", "בזכות", "מקור עסקה", "פירוט נוסף", "תאור מורחב",
                  "4 ספרות", "fingerprint", "קטגוריה", notes, statement_month,
                  ingested_at, excluded_from_calculations
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    date,
                    debit,
                    credit,
                    source,
                    detail,
                    extended,
                    card,
                    fingerprint,
                    category,
                    statement_month,
                    date,
                    excluded,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def test_schema_v19_override_table(self) -> None:
        db = self._fresh_db()
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='installment_plan_override'"
            ).fetchone()
            self.assertIsNotNone(row)
            max_v = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            self.assertGreaterEqual(max_v, 20)
        finally:
            conn.close()

    def test_tagged_installment_projects_remaining(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        # Purchase 2024-09-05 → base statement 2024-10; payments 1..3 of 12 observed
        for x in (1, 2, 3):
            self._insert(
                db,
                date="2024-09-05",
                debit=100.0,
                source="KSP Store",
                detail=f"תשלום {x} מתוך 12",
                fingerprint=f"fp-inst-{x}",
                category="Shopping",
            )

        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2025-01"]}
        )
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        plans = payload["installments"]["plans"]
        self.assertEqual(len(plans), 1)
        p = plans[0]
        self.assertEqual(p["paid_count"], 3)
        self.assertEqual(p["total_count"], 12)
        self.assertEqual(p["remaining_count"], 9)
        self.assertEqual(p["remaining_ils"], 900.0)
        self.assertEqual(p["last_month"], "2025-09")  # base Oct 2024 + 11
        # Payments 1–3 observed (Oct–Dec 2024); as_of Jan 2025 → payment 4 expected, not missing
        self.assertEqual(p["status"], "active")
        self.assertEqual(p["next_month"], "2025-01")
        # Not in recurring
        self.assertEqual(payload["recurring"]["fees"], [])

    def test_summary_query_omits_plan_rows(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="KSP Store",
            detail="תשלום 1 מתוך 12",
            fingerprint="fp-sum-1",
            category="Shopping",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2025-01"], "summary": ["1"]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(payload["installments"]["plans"], [])
        self.assertEqual(payload["recurring"]["fees"], [])
        self.assertGreaterEqual(payload["installments"]["summary"]["active"], 1)

    def test_charge_date_rows_cluster_as_one_plan(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=80.0,
            source="SplitDate Shop",
            detail="תשלום 1 מתוך 4",
            fingerprint="fp-cd-1",
        )
        self._insert(
            db,
            date="2024-10-18",
            debit=80.0,
            source="SplitDate Shop",
            detail="תשלום 2 מתוך 4",
            fingerprint="fp-cd-2",
        )
        code, payload = installment_plans.list_installment_plans({"as_of": ["2024-11"]})
        self.assertEqual(code, 200)
        plans = [
            p
            for p in payload["installments"]["plans"]
            if p.get("merchant") == "SplitDate Shop"
        ]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["paid_count"], 2)
        self.assertEqual(plans[0]["total_count"], 4)

    def test_installment_active_when_caught_up(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        # as_of = first statement month → payment 1 due, not missing
        self._insert(
            db,
            date="2024-09-05",
            debit=50.0,
            source="Shop",
            detail="תשלום 1 מתוך 3",
            fingerprint="fp-a1",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-10"]}
        )
        self.assertEqual(code, 200)
        p = payload["installments"]["plans"][0]
        self.assertEqual(p["status"], "active")
        self.assertEqual(p["remaining_count"], 2)
        self.assertEqual(p["next_month"], "2024-11")
        self.assertEqual(p["due_this_month"], 50.0)  # observed payment 1 in Oct

    def test_parion_marks_paid_off(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="Shop",
            detail="תשלום 1 מתוך 6",
            fingerprint="fp-p1",
        )
        self._insert(
            db,
            date="2024-09-05",
            debit=500.0,
            source="Shop",
            detail="פרעון תשלום",
            fingerprint="fp-parion",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-11"]}
        )
        self.assertEqual(code, 200)
        p = payload["installments"]["plans"][0]
        self.assertEqual(p["status"], "paid_off")
        self.assertEqual(p["remaining_ils"], 0.0)

    def test_recurring_indefinite_no_leftover(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        for i, ym in enumerate(["2024-01-15", "2024-02-15", "2024-03-15", "2024-04-15", "2024-05-15", "2024-06-15"]):
            self._insert(
                db,
                date=ym,
                debit=29.9,
                source="Netflix IL",
                detail="מנוי",
                fingerprint=f"fp-nf-{i}",
            )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-06"]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(payload["installments"]["plans"], [])
        fees = payload["recurring"]["fees"]
        self.assertEqual(len(fees), 1)
        f = fees[0]
        self.assertEqual(f["status"], "active")
        self.assertEqual(f["months_seen"], 6)
        self.assertEqual(f["next_expected"], "2024-07")
        self.assertNotIn("remaining_ils", f)
        self.assertNotIn("last_month", f)
        self.assertNotIn("total_count", f)
        self.assertAlmostEqual(f["monthly_amount"], 29.9)
        self.assertAlmostEqual(f["lifetime_paid"], 29.9 * 6)
        self.assertEqual(f.get("amount_mode"), "fixed")

    def test_tagged_excluded_from_recurring(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        for x, ym in enumerate(["2024-01-01", "2024-02-01", "2024-03-01"], start=1):
            self._insert(
                db,
                date="2023-12-10",
                debit=200.0,
                source="Furniture",
                detail=f"תשלום {x} מתוך 12",
                fingerprint=f"fp-fur-{x}",
                statement_month=ym[:7],
            )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-03"]}
        )
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(payload["installments"]["plans"]), 1)
        self.assertEqual(payload["recurring"]["fees"], [])

    def test_recurring_lapsed(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        for i, ym in enumerate(["2023-01-01", "2023-02-01", "2023-03-01"]):
            self._insert(
                db,
                date=ym,
                debit=40.0,
                source="Old Sub",
                fingerprint=f"fp-old-{i}",
            )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-06"]}
        )
        self.assertEqual(code, 200)
        fees = payload["recurring"]["fees"]
        self.assertEqual(len(fees), 1)
        self.assertEqual(fees[0]["status"], "lapsed")
        self.assertIsNone(fees[0]["next_expected"])

    def test_hide_and_include_hidden(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=80.0,
            source="HideMe",
            detail="תשלום 1 מתוך 4",
            fingerprint="fp-hide",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-10"]}
        )
        pid = payload["installments"]["plans"][0]["plan_id"]

        code, patched = installment_plans.patch_installment_plan(
            pid, b'{"hidden": true}'
        )
        self.assertEqual(code, 200)
        self.assertTrue(patched["ok"])

        code, listed = installment_plans.list_installment_plans({"as_of": ["2024-10"]})
        self.assertEqual(listed["installments"]["plans"], [])

        code, shown = installment_plans.list_installment_plans(
            {"as_of": ["2024-10"], "include_hidden": ["1"]}
        )
        self.assertEqual(len(shown["installments"]["plans"]), 1)
        self.assertTrue(shown["installments"]["plans"][0]["hidden"])

    def test_merge_cross_section_rejected(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="A",
            detail="תשלום 1 מתוך 3",
            fingerprint="fp-a",
        )
        for i, ym in enumerate(["2024-01-01", "2024-02-01", "2024-03-01"]):
            self._insert(
                db,
                date=ym,
                debit=15.0,
                source="B Sub",
                fingerprint=f"fp-b-{i}",
            )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-10"]}
        )
        inst_id = payload["installments"]["plans"][0]["plan_id"]
        rec_id = payload["recurring"]["fees"][0]["plan_id"]

        code, err = installment_plans.patch_installment_plan(
            inst_id, f'{{"merged_into": "{rec_id}"}}'.encode()
        )
        self.assertEqual(code, 400)
        self.assertEqual(err["error"], "cross_section_merge")

    def test_merge_same_section(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="ShopA",
            detail="תשלום 1 מתוך 3",
            fingerprint="fp-ma",
        )
        self._insert(
            db,
            date="2024-08-01",
            debit=50.0,
            source="ShopB",
            detail="תשלום 1 מתוך 2",
            fingerprint="fp-mb",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-10"]}
        )
        plans = payload["installments"]["plans"]
        self.assertEqual(len(plans), 2)
        a, b = plans[0]["plan_id"], plans[1]["plan_id"]

        code, patched = installment_plans.patch_installment_plan(
            a, f'{{"merged_into": "{b}"}}'.encode()
        )
        self.assertEqual(code, 200)
        listed = installment_plans.list_installment_plans({"as_of": ["2024-10"]})[1]
        ids = {p["plan_id"] for p in listed["installments"]["plans"]}
        self.assertIn(b, ids)
        self.assertNotIn(a, ids)
        target = next(p for p in listed["installments"]["plans"] if p["plan_id"] == b)
        sources = target.get("merged_sources") or []
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["plan_id"], a)

        code, _ = installment_plans.patch_installment_plan(
            a, b'{"merged_into": null}'
        )
        self.assertEqual(code, 200)
        listed2 = installment_plans.list_installment_plans({"as_of": ["2024-10"]})[1]
        ids2 = {p["plan_id"] for p in listed2["installments"]["plans"]}
        self.assertEqual(ids2, {a, b})
        target2 = next(p for p in listed2["installments"]["plans"] if p["plan_id"] == b)
        self.assertFalse(target2.get("merged_sources"))

    def test_split_by_last4(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="Duo",
            detail="תשלום 1 מתוך 4",
            fingerprint="fp-c1",
            card="1111",
        )
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="Duo",
            detail="תשלום 2 מתוך 4",
            fingerprint="fp-c2",
            card="2222",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-10"]}
        )
        self.assertEqual(len(payload["installments"]["plans"]), 1)
        pid = payload["installments"]["plans"][0]["plan_id"]

        code, patched = installment_plans.patch_installment_plan(
            pid, b'{"split_by": "last4"}'
        )
        self.assertEqual(code, 200)
        listed = installment_plans.list_installment_plans({"as_of": ["2024-10"]})[1]
        plans = listed["installments"]["plans"]
        self.assertEqual(len(plans), 2)
        self.assertTrue(all("|card:" in p["plan_id"] for p in plans))

    def test_gap_status(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        # Payments 1 and 3 observed; hole at 2 after starting point → gapped
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="GapShop",
            detail="תשלום 1 מתוך 6",
            fingerprint="fp-gap1",
        )
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="GapShop",
            detail="תשלום 3 מתוך 6",
            fingerprint="fp-gap3",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-12"]}
        )
        p = payload["installments"]["plans"][0]
        self.assertEqual(p["status"], "gapped")
        missing = [t for t in p["timeline"] if t["state"] == "missing"]
        self.assertTrue(any(t["x"] == 2 for t in missing))

    def test_missing_before_first_observed_is_cutoff_not_gap(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        # Ledger starts mid-plan: only payment 3 of 6 — early 1–2 are cutoff, not gaps
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="LateStart",
            detail="תשלום 3 מתוך 6",
            fingerprint="fp-late3",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-12"]}
        )
        p = payload["installments"]["plans"][0]
        self.assertEqual(p["status"], "active")
        self.assertEqual(p["first_observed_x"], 3)
        before = [t for t in p["timeline"] if t["state"] == "before_cutoff"]
        self.assertEqual({t["x"] for t in before}, {1, 2})
        # Remaining = 4,5,6 (expected after Dec? as_of Dec = payment 3's month is Dec?
        # base = Oct 2024; x=1 Oct, 2 Nov, 3 Dec, 4 Jan...
        # as_of Dec 2024: payment 3 observed in Dec; 4+ expected → remaining 3
        self.assertEqual(p["remaining_count"], 3)
        self.assertFalse(any(t["state"] == "missing" for t in p["timeline"]))

    def test_settle_rest_marks_paid_off(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        self._insert(
            db,
            date="2024-09-05",
            debit=100.0,
            source="SettleMe",
            detail="תשלום 1 מתוך 6",
            fingerprint="fp-set1",
        )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-12"]}
        )
        p = payload["installments"]["plans"][0]
        self.assertEqual(p["status"], "gapped")
        pid = p["plan_id"]
        self.assertGreater(p["remaining_ils"], 0)

        code, patched = installment_plans.patch_installment_plan(
            pid, b'{"settled": true}'
        )
        self.assertEqual(code, 200)
        listed = installment_plans.list_installment_plans({"as_of": ["2024-12"]})[1]
        s = listed["installments"]["plans"][0]
        self.assertTrue(s["settled"])
        self.assertEqual(s["status"], "paid_off")
        self.assertEqual(s["remaining_ils"], 0.0)
        self.assertEqual(s["remaining_count"], 0)
        self.assertIsNone(s["next_month"])
        settled_slots = [t for t in s["timeline"] if t["state"] == "settled"]
        self.assertGreaterEqual(len(settled_slots), 3)
        self.assertFalse(any(t["state"] == "missing" for t in s["timeline"]))
        self.assertFalse(any(t["state"] == "expected" for t in s["timeline"]))

        # Unsettle restores leftover
        code, _ = installment_plans.patch_installment_plan(pid, b'{"settled": false}')
        self.assertEqual(code, 200)
        again = installment_plans.list_installment_plans({"as_of": ["2024-12"]})[1]
        self.assertEqual(again["installments"]["plans"][0]["status"], "gapped")
        self.assertFalse(again["installments"]["plans"][0]["settled"])

    def test_settle_rejected_for_recurring(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        for i, ym in enumerate(["2024-01-01", "2024-02-01", "2024-03-01"]):
            self._insert(
                db,
                date=ym,
                debit=20.0,
                source="SubOnly",
                fingerprint=f"fp-sub-{i}",
            )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-03"]}
        )
        rid = payload["recurring"]["fees"][0]["plan_id"]
        code, err = installment_plans.patch_installment_plan(
            rid, b'{"settled": true}'
        )
        self.assertEqual(code, 400)
        self.assertEqual(err["error"], "settled_installments_only")

    def test_recurring_merge_reprojects_amounts(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        # Two amount bands for same merchant — merge into the smaller band
        for i, ym in enumerate(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]):
            self._insert(
                db,
                date=ym,
                debit=100.0,
                source="MergeMe",
                fingerprint=f"fp-m100-{i}",
            )
        for i, ym in enumerate(["2024-05-01", "2024-06-01", "2024-07-01", "2024-08-01"]):
            self._insert(
                db,
                date=ym,
                debit=500.0,
                source="MergeMe",
                fingerprint=f"fp-m500-{i}",
            )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-08"]}
        )
        fees = payload["recurring"]["fees"]
        self.assertEqual(len(fees), 2)
        by_amt = {round(f["monthly_amount"], 2): f for f in fees}
        self.assertIn(100.0, by_amt)
        self.assertIn(500.0, by_amt)
        small = by_amt[100.0]["plan_id"]
        big = by_amt[500.0]["plan_id"]

        code, _ = installment_plans.patch_installment_plan(
            big, f'{{"merged_into": "{small}"}}'.encode()
        )
        self.assertEqual(code, 200)
        listed = installment_plans.list_installment_plans({"as_of": ["2024-08"]})[1]
        fees2 = listed["recurring"]["fees"]
        self.assertEqual(len(fees2), 1)
        f = fees2[0]
        self.assertEqual(f["plan_id"], small)
        self.assertEqual(f["months_seen"], 8)
        self.assertEqual(f["lifetime_paid"], 100.0 * 4 + 500.0 * 4)
        # Latest month (2024-08) total is 500
        self.assertEqual(f["monthly_amount"], 500.0)
        self.assertEqual(f["last_month_amount"], 500.0)
        self.assertEqual(f["typical_monthly_amount"], 300.0)
        self.assertEqual(f["first_seen"], "2024-01")
        self.assertEqual(f["last_seen"], "2024-08")
        self.assertNotIn("_rows", f)
        sources = f.get("merged_sources") or []
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["plan_id"], big)

        code, _ = installment_plans.patch_installment_plan(
            big, b'{"merged_into": null}'
        )
        self.assertEqual(code, 200)
        fees3 = installment_plans.list_installment_plans({"as_of": ["2024-08"]})[1][
            "recurring"
        ]["fees"]
        self.assertEqual(len(fees3), 2)

    def test_variable_amount_monthly_recurring(self) -> None:
        """Bank-transfer style: same merchant, monthly cadence, amounts vary >±10."""
        from api import installment_plans

        db = self._fresh_db()
        amounts = [350.0, 420.0, 280.0, 900.0, 510.0, 475.0]
        for i, (ym, amt) in enumerate(
            zip(
                [
                    "2024-01-05",
                    "2024-02-05",
                    "2024-03-05",
                    "2024-04-05",
                    "2024-05-05",
                    "2024-06-05",
                ],
                amounts,
            )
        ):
            self._insert(
                db,
                date=ym,
                debit=amt,
                source="ד. אביטן",
                detail="העברה",
                category="משק בית",
                fingerprint=f"fp-var-{i}",
            )
        code, payload = installment_plans.list_installment_plans(
            {"as_of": ["2024-06"]}
        )
        self.assertEqual(code, 200)
        fees = payload["recurring"]["fees"]
        self.assertEqual(len(fees), 1)
        f = fees[0]
        self.assertEqual(f["amount_mode"], "variable")
        self.assertIn("|var", f["plan_id"])
        self.assertEqual(f["merchant"], "ד. אביטן")
        self.assertEqual(f["category"], "משק בית")
        self.assertEqual(f["months_seen"], 6)
        self.assertEqual(f["status"], "active")
        self.assertAlmostEqual(f["lifetime_paid"], sum(amounts))
        # Latest month (2024-06) = 475
        self.assertEqual(f["monthly_amount"], 475.0)
        self.assertEqual(f["last_month_amount"], 475.0)
        self.assertEqual(f["typical_monthly_amount"], 447.5)
        self.assertEqual(payload["recurring"]["summary"]["variable_active"], 1)
        self.assertGreaterEqual(len(payload["recurring"]["summary"]["top_active"]), 1)

    def test_variable_split_by_category(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        for i, ym in enumerate(["2024-01-05", "2024-02-05", "2024-03-05", "2024-04-05"]):
            self._insert(
                db,
                date=ym,
                debit=100.0 + i * 20,
                source="העברה דיגיטל",
                extended="העברה אל: יובל ממן 18-001-217614968 בין חשבונותיי",
                category="משק בית",
                fingerprint=f"fp-cat-a-{i}",
            )
        for i, ym in enumerate(["2024-01-10", "2024-02-10", "2024-03-10", "2024-04-10"]):
            self._insert(
                db,
                date=ym,
                debit=500.0 + i * 50,
                source="העברה דיגיטל",
                extended="העברה אל: יובל ממן 18-001-217614968 חופשה",
                category="חופשה תאילנד",
                fingerprint=f"fp-cat-b-{i}",
            )
        fees = installment_plans.list_installment_plans({"as_of": ["2024-04"]})[1][
            "recurring"
        ]["fees"]
        self.assertEqual(len(fees), 2)
        cats = {f["category"] for f in fees}
        self.assertEqual(cats, {"משק בית", "חופשה תאילנד"})
        self.assertTrue(all(f["amount_mode"] == "variable" for f in fees))

    def test_variable_split_by_transfer_recipient(self) -> None:
        from api import installment_plans

        db = self._fresh_db()
        for i, ym in enumerate(["2024-01-05", "2024-02-05", "2024-03-05", "2024-04-05"]):
            self._insert(
                db,
                date=ym,
                debit=200.0 + i * 10,
                source="הע. אינטרנט",
                extended="העברה אל: יובל ממן ושחף שב 18-001-217614968 בין חשבונותיי",
                category="משק בית",
                fingerprint=f"fp-rec-a-{i}",
            )
        for i, ym in enumerate(["2024-01-08", "2024-02-08", "2024-03-08", "2024-04-08"]):
            self._insert(
                db,
                date=ym,
                debit=300.0 + i * 15,
                source="הע. אינטרנט",
                extended="העברה אל: סיגל ואילן ש 10-954-002994196 שכר לימוד",
                category="משק בית",
                fingerprint=f"fp-rec-b-{i}",
            )
        fees = installment_plans.list_installment_plans({"as_of": ["2024-04"]})[1][
            "recurring"
        ]["fees"]
        self.assertEqual(len(fees), 2)
        labels = {f.get("extra_label") or "" for f in fees}
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("217614968" in lb for lb in labels))
        self.assertTrue(any("002994196" in lb for lb in labels))


if __name__ == "__main__":
    unittest.main()
