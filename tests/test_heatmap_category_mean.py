"""Heatmap category average: recent active months only (see api.heatmap)."""

import unittest

import pandas as pd

from api.heatmap import category_mean_recent_active


class TestCategoryMeanRecentActive(unittest.TestCase):
    def test_net_skips_leading_zeros_uses_all_active_months(self) -> None:
        # Newest-first index: many zeros then five negatives — mean over those five only.
        vals = [0.0] * 20 + [-2.26, -159.15, -780.98, -1256.31, -1962.25]
        pool = [f"{y}-{m:02d}" for y in (2025, 2024, 2023) for m in range(1, 13)]
        idx = sorted(pool, reverse=True)[: len(vals)]
        col = pd.Series(vals, index=idx)
        m = category_mean_recent_active(col, "net")
        exp = sum([-2.26, -159.15, -780.98, -1256.31, -1962.25]) / 5.0
        self.assertAlmostEqual(m, exp, places=2)

    def test_expense_only_positive_counts_as_active(self) -> None:
        idx = sorted([f"2024-{i:02d}" for i in range(1, 5)], reverse=True)
        col = pd.Series([100.0, 0.0, 50.0, 0.0], index=idx)
        m = category_mean_recent_active(col, "expense")
        self.assertAlmostEqual(m, 75.0)

    def test_caps_at_twelve_active_months(self) -> None:
        # 13 consecutive active months (newest = 13); average uses only the 12 newest (13…2).
        idx = sorted([f"2024-{i:02d}" for i in range(1, 13)] + ["2023-12"], reverse=True)
        vals = list(range(13, 0, -1))
        col = pd.Series([float(v) for v in vals], index=idx)
        m = category_mean_recent_active(col, "expense")
        self.assertAlmostEqual(m, sum(range(2, 14)) / 12.0)


if __name__ == "__main__":
    unittest.main()
