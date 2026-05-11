"""Smoke tests for the FastAPI control app (Starlette TestClient + httpx ASGITransport)."""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from starlette.testclient import TestClient

from api.main import create_app


class FastApiControlSmokeTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_status_and_ledger_meta_and_bad_job_json(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            with TestClient(create_app()) as client:
                r = client.get("/api/status")
                self.assertEqual(r.status_code, 200)
                self.assertIn("running", r.json())

                r2 = client.get("/api/ledger-meta")
                self.assertEqual(r2.status_code, 200)
                self.assertIn("ok", r2.json())

                r_drop = client.get("/api/transaction-drop-rules")
                self.assertEqual(r_drop.status_code, 200)
                dj = r_drop.json()
                self.assertEqual(dj.get("version"), 1)
                self.assertIsInstance(dj.get("rules"), list)

                r3 = client.post(
                    "/api/jobs/run",
                    content=b"not-json",
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(r3.status_code, 400)
                self.assertIn("error", r3.json())

    def test_httpx_asgi_transport_status(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)

            async def _run() -> int:
                transport = httpx.ASGITransport(app=create_app())
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    res = await client.get("/api/status")
                    return res.status_code

            self.assertEqual(asyncio.run(_run()), 200)


if __name__ == "__main__":
    unittest.main()
