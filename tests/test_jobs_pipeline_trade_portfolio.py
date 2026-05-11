"""Web job runner: pipeline action with process_trade_portfolio."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from api import jobs

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "trade_portfolio_minimal.xls"


class JobsPipelineTradePortfolioTests(unittest.TestCase):
    def test_pipeline_imports_newest_lti_workbook(self) -> None:
        lines: list[str] = []

        def sink(msg: str) -> None:
            lines.append(msg)

        with tempfile.TemporaryDirectory() as tmp:
            inbox = os.path.join(tmp, "tp_inbox")
            os.makedirs(inbox)
            db_path = os.path.join(tmp, "ledger.sqlite")
            shutil.copy(FIXTURE, os.path.join(inbox, "trade.xls"))

            with patch.object(config, "trade_portfolio_inbox_dir", inbox), patch.object(
                config, "ledger_db_file", db_path
            ):
                jobs.run_action(
                    "pipeline",
                    {
                        "download_enabled": False,
                        "route_inbox": False,
                        "process_holdings": False,
                        "process_transactions": False,
                        "process_trade_portfolio": True,
                    },
                    sink=sink,
                )

            joined = "\n".join(lines)
            self.assertIn("TRADE PORTFOLIO IMPORT: done", joined)
            self.assertIn("rows=2", joined)
            conn = sqlite3.connect(db_path)
            try:
                n = conn.execute("SELECT COUNT(*) FROM trade_portfolio_position").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
