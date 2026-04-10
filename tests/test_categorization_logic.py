"""
Automated tests for categorize_storename() branches (mock UI, temp files).

Run from repo root:
  python -m unittest tests.test_categorization_logic -v

To exercise the real pipeline against a throwaway tree, set FINANCE_WORKSPACE_ROOT to a
temp directory, ``importlib.reload`` on ``config``, then copy fixtures under that root.

Manual browser UI (separate): set FINANCE_CATEGORIZE_UI=http and run the app (GET /api/next), or:
  python -c "from tests.test_categorization_logic import manual_http_smoke; manual_http_smoke()"
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import config
from categorization.categorizer import CategorizeFile
from categorization.interactive.http_server import HttpCategorizationHandler
from categorization.interactive.prompts import (
    FluidStorePrompt,
    NewStorePrompt,
    ResolveStaticPrompt,
)

# Minimal columns required for CategorizeFile.__init__ and categorize_storename.
# Includes legacy **מזהה עסקה** (not a ledger column); production ledger paths use **fingerprint**.
_COMPILED_COLS = [
    "מזהה עסקה",
    "מקור עסקה",
    "תאריך",
    "בחובה",
    "בזכות",
    "קטגוריה",
]


def _minimal_compiled_row(tid="1", store="TestStore", cat=""):
    return {
        "מזהה עסקה": tid,
        "מקור עסקה": store,
        "תאריך": "2024-01-01",
        "בחובה": 0,
        "בזכות": 10,
        "קטגוריה": cat,
    }


class ScriptedHandler:
    """Returns queued answers for each prompt type (for deterministic tests)."""

    def __init__(self) -> None:
        self.fluid: list[str] = []
        self.resolve: list[int] = []
        self.new_store: list[tuple[str, int]] = []

    def prompt_fluid_store(self, prompt: FluidStorePrompt) -> str:
        return self.fluid.pop(0)

    def prompt_resolve_static(self, prompt: ResolveStaticPrompt) -> int:
        return self.resolve.pop(0)

    def prompt_new_store(self, prompt: NewStorePrompt) -> tuple[str, int]:
        return self.new_store.pop(0)


class CategorizeStorenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stores_path = os.path.join(self._tmp.name, "stores_to_categories.csv")
        self.compiled_path = os.path.join(self._tmp.name, "compiled.csv")

    def _write_compiled(self, row: dict) -> None:
        pd.DataFrame([row], columns=_COMPILED_COLS).to_csv(self.compiled_path, index=False)

    def _write_stores(self, rows: list[dict]) -> None:
        df = pd.DataFrame(rows)
        df.to_csv(self.stores_path, index=False)

    def _cat(self, row: dict, handler: ScriptedHandler) -> str:
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path, interaction_handler=handler)
            cf.load_stores()
            return cf.categorize_storename(row, method="input", interaction_handler=handler)

    def test_load_stores_creates_missing_file(self) -> None:
        missing = os.path.join(self._tmp.name, "nested", "stores_to_categories.csv")
        self.assertFalse(os.path.isfile(missing))
        self._write_compiled(_minimal_compiled_row())
        with patch.object(config, "stores_to_categories_file", missing):
            cf = CategorizeFile(self.compiled_path)
            cf.load_stores()
        self.assertTrue(os.path.isfile(missing))
        self.assertEqual(
            list(cf.stores_df.columns),
            ["store_name", "category", "is_static"],
        )
        self.assertEqual(len(cf.stores_df), 0)

    def test_static_store_returns_category_without_prompt(self) -> None:
        h = ScriptedHandler()
        self._write_stores(
            [{"store_name": "StoreA", "category": "Rent", "is_static": 1}]
        )
        row = _minimal_compiled_row(store="StoreA")
        out = self._cat(row, h)
        self.assertEqual(out, "Rent")
        self.assertFalse(h.fluid and h.resolve and h.new_store)

    def test_auto_finds_static_store_not_only_first_row(self) -> None:
        """Regression: auto mode must scan the full stores table, not stop after row 0."""
        self._write_stores(
            [
                {"store_name": "ZZZ_Other", "category": "Misc", "is_static": 1},
                {"store_name": "TargetStore", "category": "Fuel", "is_static": 1},
            ]
        )
        row = _minimal_compiled_row(store="TargetStore")
        with patch.object(config, "stores_to_categories_file", self.stores_path):
            self._write_compiled(row)
            cf = CategorizeFile(self.compiled_path, interaction_handler=ScriptedHandler())
            cf.load_stores()
            out = cf.categorize_storename(row, method="auto")
        self.assertEqual(out, "Fuel")

    def test_fluid_store_picks_existing_dynamic_category(self) -> None:
        h = ScriptedHandler()
        h.fluid.append("Food")
        self._write_stores(
            [
                {"store_name": "FluidShop", "category": "Food", "is_static": 0},
                {"store_name": "FluidShop", "category": "Drinks", "is_static": 0},
            ]
        )
        row = _minimal_compiled_row(store="FluidShop")
        out = self._cat(row, h)
        self.assertEqual(out, "Food")

    def test_fluid_store_adds_new_category_row(self) -> None:
        h = ScriptedHandler()
        h.fluid.append("BrandNew")
        self._write_stores(
            [{"store_name": "FluidOnly", "category": "Old", "is_static": 0}],
        )
        row = _minimal_compiled_row(store="FluidOnly")
        out = self._cat(row, h)
        self.assertEqual(out, "BrandNew")
        after = pd.read_csv(self.stores_path)
        self.assertGreaterEqual(len(after), 2)
        match = after[after["store_name"] == "FluidOnly"]
        self.assertIn("BrandNew", match["category"].tolist())

    def test_new_store_prompt_adds_row(self) -> None:
        h = ScriptedHandler()
        h.new_store.append(("Groceries", 1))
        self._write_stores(
            [{"store_name": "Other", "category": "X", "is_static": 1}],
        )
        row = _minimal_compiled_row(store="UnknownShop")
        out = self._cat(row, h)
        self.assertEqual(out, "Groceries")
        after = pd.read_csv(self.stores_path)
        row_new = after[after["store_name"] == "UnknownShop"]
        self.assertEqual(len(row_new), 1)
        self.assertEqual(int(row_new["is_static"].iloc[0]), 1)

    def test_resolve_ambiguous_is_static(self) -> None:
        h = ScriptedHandler()
        h.resolve.append(1)
        self._write_stores(
            [{"store_name": "Weird", "category": "Misc", "is_static": -1}],
        )
        row = _minimal_compiled_row(store="Weird")
        out = self._cat(row, h)
        self.assertEqual(out, "Misc")
        after = pd.read_csv(self.stores_path)
        self.assertEqual(int(after.loc[after["store_name"] == "Weird", "is_static"].iloc[0]), 1)

    def test_auto_categorize_does_not_read_fingerprint_sidecar_when_ledger_file_exists(self) -> None:
        """MIG-E3: CSV-mode categorizer must not touch fingerprint_db.csv if ledger.sqlite exists."""
        paths: list[str] = []
        real_read_csv = pd.read_csv

        def _wrap(path, *args, **kwargs):
            paths.append(os.path.normpath(os.path.abspath(str(path))))
            return real_read_csv(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            compiled = os.path.join(d, "compiled.csv")
            pd.DataFrame(
                [
                    {
                        "fingerprint": "fp1",
                        "מקור עסקה": "S",
                        "תאריך": "2024-01-01",
                        "בחובה": 0,
                        "בזכות": 1,
                        "קטגוריה": "",
                    }
                ]
            ).to_csv(compiled, index=False)
            ledger = os.path.join(d, "ledger.sqlite")
            open(ledger, "wb").close()
            fp_sidecar = os.path.join(d, "fingerprint_db.csv")
            pd.DataFrame([{"fingerprint": "fp1", "category": "NeverUsed"}]).to_csv(
                fp_sidecar, index=False
            )
            stores = os.path.join(d, "stores.csv")
            pd.DataFrame(columns=["store_name", "category", "is_static"]).to_csv(
                stores, index=False
            )
            os.makedirs(os.path.join(d, "bak"), exist_ok=True)
            bak = os.path.join(d, "bak", "transaction_category.csv")
            with patch.object(config, "ledger_db_file", ledger), patch.object(
                config, "fingerprint_db_file", fp_sidecar
            ), patch.object(config, "stores_to_categories_file", stores), patch.object(
                config, "transaction_category_file", bak
            ), patch.object(pd, "read_csv", side_effect=_wrap):
                cf = CategorizeFile(compiled)
                cf.load_stores()
                cf.auto_categorize()
        abs_fp = os.path.normpath(os.path.abspath(fp_sidecar))
        self.assertNotIn(abs_fp, paths)


class HttpHandlerIntegrationTest(unittest.TestCase):
    """One thread blocks on prompt; another answers via the local HTTP API."""

    def test_http_fluid_prompt_roundtrip(self) -> None:
        h = HttpCategorizationHandler(host="127.0.0.1", port=0, open_browser=False)
        self.addCleanup(h.close)

        prompt = FluidStorePrompt(
            store_name="HttpStore",
            date="2024-02-02",
            expense=0,
            income=5,
            details=None,
            digits=None,
            dynamic_categories=("A", "B"),
            all_categories=("A", "B", "C"),
            prompt_id="test-pid-1",
        )

        result_holder: list[str] = []

        def client() -> None:
            time.sleep(0.15)
            import urllib.error
            import urllib.request

            base = h.base_url
            for _ in range(50):
                try:
                    r = urllib.request.urlopen(base + "api/next", timeout=0.5)
                    data = json.loads(r.read().decode("utf-8"))
                except (urllib.error.URLError, json.JSONDecodeError):
                    time.sleep(0.05)
                    continue
                pending = data.get("pending", data)
                if pending.get("kind") == "fluid":
                    body = json.dumps(
                        {"kind": "fluid", "prompt_id": pending["prompt_id"], "category": "B"}
                    ).encode("utf-8")
                    req = urllib.request.Request(
                        base + "api/respond",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=2)
                    return
                time.sleep(0.05)
            raise AssertionError("timed out waiting for fluid prompt in /api/next")

        t = threading.Thread(target=client, daemon=True)
        t.start()
        try:
            result_holder.append(h.prompt_fluid_store(prompt))
        finally:
            t.join(timeout=5)
        self.assertEqual(result_holder[0], "B")

    def test_display_dict_json_matches_real_csv_cell_types(self) -> None:
        """Pandas/numpy scalars from read_csv must serialize for /api/next (browser JSON.parse)."""
        p = FluidStorePrompt(
            store_name=np.str_("Shop"),
            date=pd.Timestamp("2024-06-01"),
            expense=np.int64(0),
            income=np.float64(3.5),
            details=np.float64(np.nan),
            digits=None,
            dynamic_categories=("Food",),
            all_categories=("Food", "Other"),
            prompt_id="pid",
        )
        json.loads(json.dumps(p.to_display_dict(), ensure_ascii=False))

    def test_http_concurrent_get_index_and_api(self) -> None:
        """Browsers open multiple TCP connections; index + /api/next must not deadlock."""
        import urllib.request

        h = HttpCategorizationHandler(host="127.0.0.1", port=0, open_browser=False)
        self.addCleanup(h.close)

        prompt = FluidStorePrompt(
            store_name="Concurrent",
            date="2024-01-01",
            expense=0,
            income=1,
            details=None,
            digits=None,
            dynamic_categories=(),
            all_categories=("a",),
            prompt_id="conc-get",
        )

        def blocker() -> None:
            h.prompt_fluid_store(prompt)

        th = threading.Thread(target=blocker, daemon=True)
        th.start()
        for _ in range(100):
            time.sleep(0.02)
            if h.base_url:
                break
        else:
            self.fail("server did not start")
        base = h.base_url

        def get_index() -> bytes:
            return urllib.request.urlopen(base, timeout=3).read(300)

        def get_api() -> bytes:
            return urllib.request.urlopen(base + "api/next", timeout=3).read()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_idx = pool.submit(get_index)
            f_api = pool.submit(get_api)
            idx_body = f_idx.result()
            api_body = f_api.result()
        self.assertIn(b"<!DOCTYPE html>", idx_body)
        self.assertIn(b"fluid", api_body)

        req = urllib.request.Request(
            base + "api/respond",
            data=json.dumps(
                {"kind": "fluid", "prompt_id": "conc-get", "category": "a"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
        th.join(timeout=3)


def manual_http_smoke() -> None:
    """Interactive: prints URL; open in browser and answer one prompt (Ctrl+C to exit)."""
    print("Open the printed URL in a browser; use Ctrl+C to stop.\n")
    h = HttpCategorizationHandler(open_browser=True)
    try:
        p = FluidStorePrompt(
            store_name="Smoke",
            date="2024-01-01",
            expense=0,
            income=1,
            details=None,
            digits=None,
            dynamic_categories=("a", "b"),
            all_categories=("a", "b", "c"),
            prompt_id="smoke",
        )
        print("Answer:", h.prompt_fluid_store(p))
    finally:
        h.close()


if __name__ == "__main__":
    unittest.main()
