"""EventHub backpressure, LAN bind guard, service-account path, job threads."""

from __future__ import annotations

import importlib
import os
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient


class EventHubOverflowTests(unittest.TestCase):
    def test_full_queue_drops_oldest_keeps_subscriber(self) -> None:
        from api.utils.helpers import EventHub

        hub = EventHub(maxsize=2)
        q = hub.subscribe()
        hub.publish({"type": "log", "n": 1})
        hub.publish({"type": "log", "n": 2})
        hub.publish({"type": "log", "n": 3})
        items = []
        while True:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                break
        types = [str(x.get("type")) for x in items]
        self.assertIn("log_overflow", types)
        self.assertTrue(any(x.get("n") == 3 for x in items if x.get("type") == "log"))
        hub.publish({"type": "log", "n": 4})
        got = q.get_nowait()
        self.assertEqual(got.get("n"), 4)


class ControlBindTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_CONTROL_HTTP_HOST", None)
        os.environ.pop("FINANCE_CONTROL_ALLOW_LAN", None)
        os.environ.pop("FINANCE_CONTROL_TOKEN", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_loopback_ok(self) -> None:
        import config as config_mod

        config_mod.validate_control_bind("127.0.0.1")
        config_mod.validate_control_bind("localhost")

    def test_lan_refused_without_flag(self) -> None:
        import config as config_mod

        with self.assertRaises(RuntimeError):
            config_mod.validate_control_bind("0.0.0.0")

    def test_lan_requires_token(self) -> None:
        os.environ["FINANCE_CONTROL_ALLOW_LAN"] = "1"
        os.environ["FINANCE_CONTROL_TOKEN"] = ""
        import config as config_mod

        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        with self.assertRaises(RuntimeError):
            config_mod.validate_control_bind("0.0.0.0")

    def test_lan_ok_with_token(self) -> None:
        os.environ["FINANCE_CONTROL_ALLOW_LAN"] = "1"
        os.environ["FINANCE_CONTROL_TOKEN"] = "secret"
        import config as config_mod

        with patch("dotenv.load_dotenv"):
            importlib.reload(config_mod)
        config_mod.validate_control_bind("0.0.0.0")


class ServiceAccountPathTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod
        import providers as ps

        importlib.reload(config_mod)
        importlib.reload(ps)

    def test_rejects_path_outside_private(self) -> None:
        import config as config_mod
        import providers as ps

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)
                importlib.reload(ps)
            with self.assertRaises(ValueError):
                ps.constrain_service_account_path("/etc/passwd")
            with self.assertRaises(ValueError):
                ps.merge_put_update(
                    ps.default_document(),
                    {"google_sheets": {"service_account_json_path": "../outside.json"}},
                )
            allowed = os.path.join(config_mod.private_dir, "sa.json")
            got = ps.constrain_service_account_path(allowed)
            self.assertEqual(os.path.normcase(got), os.path.normcase(os.path.abspath(allowed)))


class JobThreadAndSheetsJobTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("FINANCE_WORKSPACE_ROOT", None)
        import config as config_mod

        importlib.reload(config_mod)

    def test_job_thread_is_not_daemon(self) -> None:
        from api.utils.helpers import ControlState

        st = ControlState()
        done = threading.Event()

        def target() -> None:
            done.set()

        st.spawn_job_thread(target, name="job-test")
        self.assertIsNotNone(st._job_thread)
        self.assertFalse(st._job_thread.daemon)
        st.join_job(timeout=5)
        self.assertTrue(done.is_set())

    def test_sheets_preview_starts_job_without_loading_ledger(self) -> None:
        import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FINANCE_WORKSPACE_ROOT"] = tmp
            with patch("dotenv.load_dotenv"):
                importlib.reload(config_mod)
            from api.main import create_app

            with patch("api.sheets.desktop_preview", return_value={"ok": True, "preview": {}}) as preview:
                with TestClient(create_app()) as client:
                    r = client.post("/api/jobs/run", json={"action": "sheets_preview", "options": {}})
                    self.assertEqual(r.status_code, 202, msg=r.text)
                    self.assertTrue(r.json().get("ok"))
                    # Worker may still be starting; wait briefly via status poll
                    for _ in range(50):
                        st = client.get("/api/status").json()
                        if not st.get("running"):
                            break
                        import time

                        time.sleep(0.05)
                preview.assert_called()


if __name__ == "__main__":
    unittest.main()
