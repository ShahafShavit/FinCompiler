from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient


class WebControlHoldingsApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_holdings_endpoints_flow(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from api.main import create_app
            from pipeline.holdings_balance import upsert_holdings_rows

            seed = [
                {"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 1000},
                {"as_of_date": "2026-05-01", "activity_type": "ניירות ערך", "balance_ils": 3000},
            ]
            out = upsert_holdings_rows(seed, config_mod.ledger_db_file, overwrite_conflicts=True)
            self.assertTrue(out.get("ok"), msg=str(out))

            with TestClient(create_app()) as client:
                meta = client.get("/api/holdings/meta")
                self.assertEqual(meta.status_code, 200)
                self.assertGreaterEqual(meta.json().get("row_count", 0), 2)

                timeline = client.get(
                    "/api/holdings/timeline?from=2026-05-01&to=2026-05-01&activity=%D7%A2%D7%95%D7%91%D7%A8%20%D7%95%D7%A9%D7%91"
                )
                self.assertEqual(timeline.status_code, 200)
                self.assertEqual(len(timeline.json().get("rows", [])), 1)

                parsed = client.post(
                    "/api/holdings/parse-paste-grid",
                    json={"text": "תאריך\tעובר ושב\n2026-05-03\t1300\n"},
                )
                self.assertEqual(parsed.status_code, 200)
                self.assertTrue(parsed.json().get("ok"))
                self.assertEqual(len(parsed.json().get("rows", [])), 1)

                conflict = client.post(
                    "/api/holdings/check-conflicts",
                    json={"rows": [{"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 999}]},
                )
                self.assertEqual(conflict.status_code, 200)
                self.assertEqual(conflict.json().get("conflict_count"), 1)

                blocked = client.post(
                    "/api/holdings/manual-upsert-batch",
                    json={
                        "rows": [{"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 999}],
                        "overwrite_conflicts": False,
                    },
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertFalse(blocked.json().get("ok"))

                saved = client.post(
                    "/api/holdings/manual-upsert-batch",
                    json={
                        "rows": [{"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 999}],
                        "overwrite_conflicts": True,
                    },
                )
                self.assertEqual(saved.status_code, 200)
                self.assertTrue(saved.json().get("ok"))

                moved = client.post(
                    "/api/holdings/move-date",
                    json={
                        "source_date": "2026-05-01",
                        "target_date": "2026-04-15",
                        "overwrite_conflicts": True,
                    },
                )
                self.assertEqual(moved.status_code, 200)
                self.assertTrue(moved.json().get("ok"))
                self.assertEqual(moved.json().get("target_date"), "2026-04-01")


if __name__ == "__main__":
    unittest.main()
