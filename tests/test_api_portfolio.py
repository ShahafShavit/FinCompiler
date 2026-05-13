"""API tests for trade-portfolio chart endpoints."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from pipeline.trade_portfolio_import import upsert_trade_portfolio_snapshot
from pipeline.trade_portfolio_queries import make_series_id


class WebPortfolioApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_portfolio_meta_and_timeseries(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from api.main import create_app

            pf = "954-037317/51"
            rows_may = [
                {
                    "snapshot_date": "2026-05-11",
                    "portfolio_account": pf,
                    "security_number": "111",
                    "security_name": "Alpha ETF",
                    "value_ils": 1000.0,
                    "quantity": 10.0,
                    "last_price": 100.0,
                },
                {
                    "snapshot_date": "2026-05-11",
                    "portfolio_account": pf,
                    "security_number": "222",
                    "security_name": "Beta ETF",
                    "value_ils": 2000.0,
                    "quantity": 20.0,
                    "last_price": 50.0,
                },
            ]
            out1 = upsert_trade_portfolio_snapshot(rows_may, db_path=config_mod.ledger_db_file)
            self.assertEqual(out1["inserted"], 2)

            rows_june = []
            for r in rows_may:
                rows_june.append(
                    {
                        **r,
                        "snapshot_date": "2026-06-01",
                        "value_ils": float(r.get("value_ils") or 0) * 1.1,
                    }
                )
            out2 = upsert_trade_portfolio_snapshot(rows_june, db_path=config_mod.ledger_db_file)
            self.assertEqual(out2["inserted"], 2)

            with TestClient(create_app()) as client:
                meta = client.get("/api/portfolio/meta")
                self.assertEqual(meta.status_code, 200)
                body = meta.json()
                self.assertTrue(body.get("ok"))
                self.assertTrue(body.get("ledger_exists"))
                self.assertEqual(body.get("min_date"), "2026-05-11")
                self.assertEqual(body.get("max_date"), "2026-06-01")
                inst = body.get("instruments") or []
                self.assertEqual(len(inst), 2)

                sid111 = make_series_id("954-037317/51", "111")
                ts = client.get(
                    "/api/portfolio/timeseries",
                    params={
                        "from": "2026-05-11",
                        "to": "2026-05-11",
                        "metric": "value_ils",
                        "series": sid111,
                    },
                )
                self.assertEqual(ts.status_code, 200)
                pts = ts.json().get("points") or []
                self.assertEqual(len(pts), 1)
                self.assertEqual(pts[0]["snapshot_date"], "2026-05-11")
                self.assertEqual(pts[0]["series_id"], sid111)
                self.assertAlmostEqual(float(pts[0]["quantity"]), 10.0, places=4)

                ts2 = client.get("/api/portfolio/timeseries?from=2026-05-11&to=2026-06-01")
                self.assertEqual(ts2.status_code, 200)
                self.assertGreaterEqual(len(ts2.json().get("points") or []), 4)

                bad = client.get("/api/portfolio/timeseries?metric=not_a_column")
                self.assertEqual(bad.status_code, 400)

    def test_timeseries_scales_price_metrics_by_multiplier_table(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from api.main import create_app

            pf = "954-SCALE/1"
            rows = [
                {
                    "snapshot_date": "2026-05-11",
                    "portfolio_account": pf,
                    "security_number": "999",
                    "security_name": "Scaled",
                    "last_price": 100.0,
                    "quantity": 1.0,
                    "value_ils": 100.0,
                    "price_multiplier": 0.01,
                },
            ]
            upsert_trade_portfolio_snapshot(rows, db_path=config_mod.ledger_db_file)

            with TestClient(create_app()) as client:
                sid = make_series_id(pf, "999")
                ts = client.get(
                    "/api/portfolio/timeseries",
                    params={
                        "from": "2026-05-11",
                        "to": "2026-05-11",
                        "metric": "last_price",
                        "series": sid,
                    },
                )
                self.assertEqual(ts.status_code, 200)
                pts = ts.json().get("points") or []
                self.assertEqual(len(pts), 1)
                self.assertAlmostEqual(float(pts[0]["value"]), 1.0, places=6)

                ts_val = client.get(
                    "/api/portfolio/timeseries",
                    params={
                        "from": "2026-05-11",
                        "to": "2026-05-11",
                        "metric": "value_ils",
                        "series": sid,
                    },
                )
                self.assertEqual(ts_val.status_code, 200)
                pv = (ts_val.json().get("points") or [{}])[0].get("value")
                self.assertAlmostEqual(float(pv), 100.0, places=6)
