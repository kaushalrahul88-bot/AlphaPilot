import unittest
from datetime import date, datetime, timedelta, timezone

from app.fred_btc_macro_regime_provider import (
    FRED_SERIES_OBSERVATIONS_URL,
    SERIES,
    FredBtcMacroRegimeProvider,
    FredMacroRegimePolicy,
    architecture_contract,
)


def _t(hour=12):
    return datetime(2026, 9, 5, hour, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        series = params["series_id"]
        latest, previous = self.values[series]
        vintage = params["realtime_start"]
        payload = {
            "observations": [
                {"realtime_start": vintage, "realtime_end": vintage, "date": "2026-09-04", "value": str(latest)},
                {"realtime_start": vintage, "realtime_end": vintage, "date": "2026-09-03", "value": str(previous)},
                {"realtime_start": vintage, "realtime_end": vintage, "date": "2026-09-02", "value": "."},
            ]
        }
        return _Response(payload)


class _Clock:
    def __init__(self, *values):
        self.values = list(values)
        self.last = values[-1]

    def __call__(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def _values():
    return {
        SERIES["BROAD_USD"]: (99.0, 100.0),
        SERIES["REAL_YIELD_10Y"]: (2.20, 2.25),
        SERIES["NASDAQ_COMPOSITE"]: (101.0, 100.0),
        SERIES["VIX"]: (18.0, 20.0),
    }


class FredBtcMacroRegimeProviderTests(unittest.TestCase):
    def test_disabled_provider_fails_before_network(self):
        client = _Client(_values())
        provider = FredBtcMacroRegimeProvider(client=client)
        with self.assertRaises(RuntimeError):
            provider.capture_regime()
        self.assertEqual(client.calls, [])

    def test_enabled_provider_requires_api_key(self):
        with self.assertRaises(ValueError):
            FredMacroRegimePolicy(enabled=True).validated()
        with self.assertRaises(ValueError):
            FredMacroRegimePolicy(lookback_days=6).validated()

    def test_live_capture_uses_documented_fred_realtime_period_and_computes_changes(self):
        client = _Client(_values())
        provider = FredBtcMacroRegimeProvider(
            FredMacroRegimePolicy(enabled=True, api_key="a" * 32),
            client=client,
            clock=_Clock(_t(12), _t(12)),
        )
        capture = provider.capture_regime()
        self.assertEqual(capture.vintage_date, date(2026, 9, 5))
        self.assertFalse(capture.historical_vintage_reconstruction)
        self.assertTrue(capture.exact_intraday_availability_proven)
        self.assertAlmostEqual(capture.broad_usd_change_pct, -1.0)
        self.assertAlmostEqual(capture.real_yield_change_bps, -5.0)
        self.assertAlmostEqual(capture.nasdaq_change_pct, 1.0)
        self.assertAlmostEqual(capture.vix_change_pct, -10.0)
        self.assertEqual(len(client.calls), 4)
        for url, params, timeout in client.calls:
            self.assertEqual(url, FRED_SERIES_OBSERVATIONS_URL)
            self.assertEqual(params["file_type"], "json")
            self.assertEqual(params["realtime_start"], "2026-09-05")
            self.assertEqual(params["realtime_end"], "2026-09-05")
            self.assertEqual(params["sort_order"], "desc")
            self.assertEqual(timeout, 10.0)

    def test_historical_vintage_is_flagged_and_does_not_claim_intraday_visibility(self):
        provider = FredBtcMacroRegimeProvider(
            FredMacroRegimePolicy(enabled=True, api_key="a" * 32),
            client=_Client(_values()),
            clock=_Clock(_t(12), _t(12)),
        )
        capture = provider.capture_regime(vintage_date=date(2026, 9, 4))
        self.assertTrue(capture.historical_vintage_reconstruction)
        self.assertFalse(capture.exact_intraday_availability_proven)
        self.assertEqual(capture.vintage_date, date(2026, 9, 4))

    def test_future_vintage_fails_closed(self):
        provider = FredBtcMacroRegimeProvider(
            FredMacroRegimePolicy(enabled=True, api_key="a" * 32),
            client=_Client(_values()),
            clock=_Clock(_t(12), _t(12)),
        )
        with self.assertRaises(ValueError):
            provider.capture_regime(vintage_date=date(2026, 9, 6))

    def test_missing_or_zero_baseline_fails_closed(self):
        values = _values()
        values[SERIES["BROAD_USD"]] = (99.0, 0.0)
        provider = FredBtcMacroRegimeProvider(
            FredMacroRegimePolicy(enabled=True, api_key="a" * 32),
            client=_Client(values),
            clock=_Clock(_t(12), _t(12)),
        )
        with self.assertRaises(ValueError):
            provider.capture_regime()

    def test_architecture_documents_daily_vintage_not_intraday_surprise(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertTrue(contract["api_key_required_when_enabled"])
        self.assertTrue(contract["real_time_period_used"])
        self.assertTrue(contract["daily_regime_not_intraday_market_feed"])
        self.assertFalse(contract["historical_same_day_intraday_visibility_proven"])
        self.assertFalse(contract["release_date_equals_exact_fred_availability_time"])
        self.assertFalse(contract["btc_direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
