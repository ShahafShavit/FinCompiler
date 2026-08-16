"""Combined portal fetches reuse one Leumi Bank session per job."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pipeline


class _FakeBank:
    """Stand-in for ``fetch.Bank`` that records construction and downloads."""

    instances: list["_FakeBank"] = []

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.downloads: list[tuple[str, object, object]] = []
        self.closed = False
        _FakeBank.instances.append(self)

    def __enter__(self) -> "_FakeBank":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def download(self, kind: str, from_date=None, to_date=None) -> bool:
        self.downloads.append((kind, from_date, to_date))
        return True

    def close(self) -> None:
        self.closed = True


class PortalFetchesLeumiSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeBank.instances.clear()

    def _resolved(self, **overrides):
        base = dict(
            bank_username="u",
            bank_password="p",
            bank_provider="leumi",
            investment_portfolio_enabled=True,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_run_portal_fetches_one_bank_for_all_leumi_kinds(self) -> None:
        with (
            patch.object(pipeline.providers, "get_resolved", return_value=self._resolved()),
            patch.object(pipeline.providers, "bank_class", return_value=_FakeBank),
            patch.object(pipeline, "fetch_transactions_max_isracard") as mock_mi,
        ):
            pipeline.run_portal_fetches(
                holdings=True,
                trade_portfolio=True,
                max_isracard=True,
                bank_credit=True,
                bank_osh=True,
                from_date="01.01.26",
                to_date="31.01.26",
            )

        mock_mi.assert_called_once()
        self.assertEqual(len(_FakeBank.instances), 1)
        bank = _FakeBank.instances[0]
        self.assertTrue(bank.closed)
        self.assertEqual(
            [k for k, _, _ in bank.downloads],
            ["holdings", "credit", "osh", "trade_portfolio"],
        )
        self.assertEqual(bank.downloads[2], ("osh", "01.01.26", "31.01.26"))

    def test_fetch_holdings_wrapper_uses_own_session(self) -> None:
        with (
            patch.object(pipeline.providers, "get_resolved", return_value=self._resolved()),
            patch.object(pipeline.providers, "bank_class", return_value=_FakeBank),
        ):
            pipeline.fetch_holdings()
            pipeline.fetch_trade_portfolio()

        self.assertEqual(len(_FakeBank.instances), 2)
        self.assertEqual(_FakeBank.instances[0].downloads, [("holdings", None, None)])
        self.assertEqual(_FakeBank.instances[1].downloads, [("trade_portfolio", None, None)])
        self.assertTrue(all(b.closed for b in _FakeBank.instances))

    def test_credit_portal_retries_capped(self) -> None:
        class Boom:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def download(self) -> None:
                raise FileNotFoundError("no file")

            def close(self) -> None:
                pass

        resolved = self._resolved()
        resolved.credit_max_enabled = True
        resolved.max_username = "m"
        resolved.max_password = "pw"
        resolved.credit_isracard_enabled = False

        with (
            patch.object(pipeline.providers, "get_resolved", return_value=resolved),
            patch.object(
                pipeline.providers,
                "credit_provider_classes",
                return_value={"max": Boom, "isracard": Boom},
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                pipeline._fetch_one_credit_portal(card_id="max", sink=None)
        self.assertIn("3 attempts", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
