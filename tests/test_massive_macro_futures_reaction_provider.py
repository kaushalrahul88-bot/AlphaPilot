import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.massive_macro_futures_reaction_provider import (
    EURO_FX_PRODUCT_CODE,
    NQ_PRODUCT_CODE,
    MassiveMacroFuturesReactionPolicy,
    MassiveMacroFuturesReactionProvider,
    architecture_contract,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
OBSERVED = RELEASE + timedelta(minutes=10)


def _ns(dt):
    return int(dt.timestamp() * 1_000_000_000)


def _bar(ticker, start_at, *, close, volume, transactions=10):
    return {
        "ticker": ticker,
        "window_start": _ns(start_at),
        "open": close,
        "high": close * 1.001,
        "low": close * 0.999,
        "close": close,
        "volume": volume,
        "transactions": transactions,
        "session_end_date": "2026-09-11",
    }


def _contract(product, ticker, days):
    return {
        "active": True,
        "date": "2026-09-11",
        "days_to_maturity": days,
        "first_trade_date": "2026-01-01",
        "last_trade_date": "2026-12-31",
        "name": f"{ticker} Future",
        "product_code": product,
        "ticker": ticker,
        "trading_venue": "XCME",
        "type": "single",
    }


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *, tie=False, stale_nq=False, missing_post=False):
        self.tie = tie
        self.stale_nq = stale_nq
        self.missing_post = missing_post
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        path = urlparse(url).path
        if path == "/futures/v1/contracts":
            product = params["product_code"]
            if product == NQ_PRODUCT_CODE:
                rows = [_contract("NQ", "NQU6", 7), _contract("NQ", "NQZ6", 98)]
            elif product == EURO_FX_PRODUCT_CODE:
                rows = [_contract("6E", "6EU6", 7), _contract("6E", "6EZ6", 98)]
            else:
                rows = []
            return _FakeResponse({"status": "OK", "results": rows})

        if path.startswith("/futures/v1/aggs/"):
            ticker = path.rsplit("/", 1)[-1]
            start_ns = int(params["window_start.gte"])
            end_ns = int(params["window_start.lt"])
            selection = end_ns == _ns(RELEASE)
            if selection:
                start = RELEASE - timedelta(minutes=1)
                if ticker == "NQU6":
                    if self.stale_nq:
                        start = RELEASE - timedelta(minutes=2)
                    volume = 100.0
                    close = 20000.0
                elif ticker == "NQZ6":
                    volume = 100.0 if self.tie else 20.0
                    close = 20100.0
                elif ticker == "6EU6":
                    volume = 200.0
                    close = 1.1000
                elif ticker == "6EZ6":
                    volume = 10.0
                    close = 1.1010
                else:
                    return _FakeResponse({"status": "OK", "results": []})
                self.assert_window(start_ns, end_ns)
                return _FakeResponse({"status": "OK", "results": [_bar(ticker, start, close=close, volume=volume)]})

            # Post-event fetch occurs only for the pre-release selected contract.
            if ticker == "NQU6":
                start = OBSERVED - timedelta(minutes=1)
                if self.missing_post:
                    start = OBSERVED - timedelta(minutes=2)
                return _FakeResponse({"status": "OK", "results": [_bar(ticker, start, close=19800.0, volume=9999.0)]})
            if ticker == "NQZ6" and self.stale_nq:
                start = OBSERVED - timedelta(minutes=1)
                return _FakeResponse({"status": "OK", "results": [_bar(ticker, start, close=19900.0, volume=9999.0)]})
            if ticker == "6EU6":
                start = OBSERVED - timedelta(minutes=1)
                return _FakeResponse({"status": "OK", "results": [_bar(ticker, start, close=1.0945, volume=9999.0)]})
            return _FakeResponse({"status": "OK", "results": []})

        raise AssertionError(f"unexpected URL {url}")

    @staticmethod
    def assert_window(start_ns, end_ns):
        if not start_ns < end_ns:
            raise AssertionError("invalid requested window")


class MassiveMacroFuturesReactionProviderTests(unittest.TestCase):
    def _provider(self, client, *, clock=None):
        return MassiveMacroFuturesReactionProvider(
            MassiveMacroFuturesReactionPolicy(enabled=True, api_key="KEY"),
            client=client,
            clock=clock or (lambda: OBSERVED + timedelta(hours=1)),
        )

    def test_selects_contracts_only_by_pre_release_volume_and_computes_reaction(self):
        client = _FakeClient()
        reaction = self._provider(client).fetch_reaction(
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
        )
        self.assertEqual(reaction.nasdaq_contract.ticker, "NQU6")
        self.assertEqual(reaction.euro_fx_contract.ticker, "6EU6")
        self.assertEqual(reaction.nasdaq_contract.pre_release_volume, 100.0)
        self.assertEqual(reaction.euro_fx_contract.pre_release_volume, 200.0)
        self.assertAlmostEqual(reaction.nasdaq_futures_return_pct, -1.0)
        self.assertAlmostEqual(reaction.eurusd_futures_return_pct, -0.5)
        self.assertAlmostEqual(reaction.usd_strength_proxy_return_pct, 0.5)
        self.assertEqual(reaction.reconstructible_available_at, OBSERVED)
        self.assertTrue(reaction.reconstructible_history)
        self.assertFalse(reaction.prospective_live_availability_proven)
        queried_post_tickers = [
            urlparse(url).path.rsplit("/", 1)[-1]
            for url, params, _ in client.calls
            if urlparse(url).path.startswith("/futures/v1/aggs/")
            and int(params["window_start.lt"]) == _ns(OBSERVED)
        ]
        self.assertEqual(sorted(queried_post_tickers), ["6EU6", "NQU6"])
        self.assertNotIn("NQZ6", queried_post_tickers)
        self.assertNotIn("6EZ6", queried_post_tickers)

    def test_equal_pre_release_volume_fails_closed_instead_of_arbitrary_roll(self):
        with self.assertRaisesRegex(ValueError, "ambiguous NQ contract selection"):
            self._provider(_FakeClient(tie=True)).fetch_reaction(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
            )

    def test_stale_front_contract_is_rejected_and_valid_alternate_is_selected(self):
        client = _FakeClient(stale_nq=True)
        reaction = self._provider(client).fetch_reaction(
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
        )
        self.assertEqual(reaction.nasdaq_contract.ticker, "NQZ6")
        self.assertEqual(reaction.nasdaq_contract.pre_release_volume, 20.0)
        self.assertAlmostEqual(reaction.nasdaq_futures_return_pct, (19900.0 / 20100.0 - 1.0) * 100.0)
        queried_post_tickers = [
            urlparse(url).path.rsplit("/", 1)[-1]
            for url, params, _ in client.calls
            if urlparse(url).path.startswith("/futures/v1/aggs/")
            and int(params["window_start.lt"]) == _ns(OBSERVED)
        ]
        self.assertIn("NQZ6", queried_post_tickers)
        self.assertNotIn("NQU6", queried_post_tickers)

    def test_missing_exact_post_event_close_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "NQ lacks exact completed reaction-window close"):
            self._provider(_FakeClient(missing_post=True)).fetch_reaction(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
            )

    def test_reaction_window_must_be_complete_before_any_network_call(self):
        client = _FakeClient()
        provider = self._provider(client, clock=lambda: OBSERVED - timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "not complete yet"):
            provider.fetch_reaction(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
            )
        self.assertEqual(client.calls, [])

    def test_disabled_provider_never_calls_network(self):
        client = _FakeClient()
        provider = MassiveMacroFuturesReactionProvider(client=client)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            provider.fetch_reaction(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
            )
        self.assertEqual(client.calls, [])

    def test_policy_requires_api_key_and_bounded_windows(self):
        with self.assertRaisesRegex(ValueError, "api_key"):
            MassiveMacroFuturesReactionPolicy(enabled=True).validated()
        with self.assertRaises(ValueError):
            MassiveMacroFuturesReactionPolicy(selection_window_minutes=4).validated()
        with self.assertRaises(ValueError):
            MassiveMacroFuturesReactionPolicy(reaction_window_minutes=31).validated()

    def test_architecture_is_replay_only_pre_event_selected_and_trade_separated(self):
        contract = architecture_contract()
        self.assertEqual(contract["nq_product_code"], "NQ")
        self.assertEqual(contract["euro_fx_product_code"], "6E")
        self.assertTrue(contract["contract_reference_is_point_in_time"])
        self.assertTrue(contract["contract_selection_uses_pre_release_data_only"])
        self.assertFalse(contract["post_release_volume_may_select_contract"])
        self.assertTrue(contract["usd_dimension_uses_inverse_eurusd_proxy"])
        self.assertFalse(contract["proxy_claimed_to_be_dxy"])
        self.assertTrue(contract["historical_replay_reconstruction_supported"])
        self.assertFalse(contract["prospective_live_availability_proven"])
        self.assertFalse(contract["live_confirmation_auto_enabled"])
        self.assertFalse(contract["continuous_contract_assumed"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["options_trade_generated"])


if __name__ == "__main__":
    unittest.main()
