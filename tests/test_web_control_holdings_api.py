from __future__ import annotations

import http.client
import importlib
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch


class WebControlHoldingsApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def _request_json(self, conn: http.client.HTTPConnection, method: str, path: str, payload: dict | None = None):
        body = b""
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        return res.status, data

    def test_holdings_endpoints_flow(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            from pipeline.holdings_csv_import import upsert_holdings_rows
            from web_control.server import ControlHTTPServer, ControlState, make_handler_class

            seed = [
                {"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 1000},
                {"as_of_date": "2026-05-01", "activity_type": "ניירות ערך", "balance_ils": 3000},
            ]
            out = upsert_holdings_rows(seed, config_mod.ledger_db_file, overwrite_conflicts=True)
            self.assertTrue(out.get("ok"), msg=str(out))

            state = ControlState()
            server = ControlHTTPServer(("127.0.0.1", 0), make_handler_class(state))
            th = threading.Thread(target=server.serve_forever, daemon=True)
            th.start()
            host, port = server.server_address
            conn = http.client.HTTPConnection(host, port, timeout=10)
            try:
                status, meta = self._request_json(conn, "GET", "/api/holdings/meta")
                self.assertEqual(status, 200)
                self.assertGreaterEqual(meta.get("row_count", 0), 2)

                status, timeline = self._request_json(
                    conn,
                    "GET",
                    "/api/holdings/timeline?from=2026-05-01&to=2026-05-01&activity=%D7%A2%D7%95%D7%91%D7%A8%20%D7%95%D7%A9%D7%91",
                )
                self.assertEqual(status, 200)
                self.assertEqual(len(timeline.get("rows", [])), 1)

                status, parsed = self._request_json(
                    conn,
                    "POST",
                    "/api/holdings/parse-paste-grid",
                    {"text": "תאריך\tעובר ושב\n2026-05-03\t1300\n"},
                )
                self.assertEqual(status, 200)
                self.assertTrue(parsed.get("ok"))
                self.assertEqual(len(parsed.get("rows", [])), 1)

                status, conflict = self._request_json(
                    conn,
                    "POST",
                    "/api/holdings/check-conflicts",
                    {"rows": [{"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 999}]},
                )
                self.assertEqual(status, 200)
                self.assertEqual(conflict.get("conflict_count"), 1)

                status, blocked = self._request_json(
                    conn,
                    "POST",
                    "/api/holdings/manual-upsert-batch",
                    {
                        "rows": [{"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 999}],
                        "overwrite_conflicts": False,
                    },
                )
                self.assertEqual(status, 409)
                self.assertFalse(blocked.get("ok"))

                status, saved = self._request_json(
                    conn,
                    "POST",
                    "/api/holdings/manual-upsert-batch",
                    {
                        "rows": [{"as_of_date": "2026-05-01", "activity_type": "עובר ושב", "balance_ils": 999}],
                        "overwrite_conflicts": True,
                    },
                )
                self.assertEqual(status, 200)
                self.assertTrue(saved.get("ok"))
            finally:
                conn.close()
                server.shutdown()
                server.server_close()
                th.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

