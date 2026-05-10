"""Sheets push-only policy + heatmap ledger source (no Google pull)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import config


class PhaseGSheetsPolicyTests(unittest.TestCase):
    def test_desktop_totals_defaults_to_totals_sheet_name_when_env_empty(self) -> None:
        with mock.patch.dict(os.environ, {"FINANCE_DESKTOP_TOTALS_SHEET": ""}):
            with mock.patch.object(config, "totals_sheet_name", "MyTotalsTab"):
                self.assertEqual(config.desktop_totals_sheet_name(), "MyTotalsTab")

    def test_gslink_has_no_pull_methods(self) -> None:
        from integrations.google_sheets import GSLink

        self.assertFalse(hasattr(GSLink, "update_local"))
        self.assertFalse(hasattr(GSLink, "pull_desktop_sync_from_cloud"))
        self.assertFalse(hasattr(GSLink, "pull_sheet_readonly_to_csv"))

    def test_totals_sheet_sync_push_helper_only(self) -> None:
        import api.totals_sheet_sync as tss

        self.assertTrue(hasattr(tss, "is_sheets_configured"))
        self.assertFalse(hasattr(tss, "ensure_totals_csv_present"))
        self.assertFalse(hasattr(tss, "refresh_totals_from_cloud"))

    def test_desktop_sheets_api_has_no_api_pull(self) -> None:
        import api.desktop_sheets_api as dsa

        self.assertFalse(hasattr(dsa, "api_pull"))
        self.assertTrue(hasattr(dsa, "api_push"))


if __name__ == "__main__":
    unittest.main()
