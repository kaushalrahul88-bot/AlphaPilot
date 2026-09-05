import unittest
from datetime import datetime, timezone

from app.tradingeconomics_macro_consensus_provider import (
    TradingEconomicsConsensusPolicy,
    TradingEconomicsConsensusTarget,
    TradingEconomicsMacroConsensusProvider,
    architecture_contract,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
SEEN = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
UPDATED = "2026-09-11T11:59:00"


def _row(
    *,
    calendar_id,
    event,
    reference_date="2026-08-31T00:00:00",
    forecast="0.2%",
    forecast_value=0.2,
    unit="%",
    date="2026-09-11T12:30:00",
    last_update=UPDATED,
    actual="",
    actual_value=None,
    date_span="0",
    source="U.S. Bureau of Labor Statistics",
    source_url="https://www.bls.gov/",
    country="United States",
):
    return {
        "CalendarId": str(calendar_id),
        "Date": date,
        "Country": country,
        "Category": event,
        "Event": event,
        "Reference": "Aug",
        "ReferenceDate": reference_date,
        "Source": source,
        "SourceURL": source_url,
        "Actual": actual,
        "ActualValue": actual_value,
        "Previous": "",
        "Forecast": forecast,
        "ForecastValue": forecast_value,
        "TEForecast": "999",
        "TEForecastValue": 999,
        "URL": "/united-states/test",
        "DateSpan": str(date_span),
        "Importance": 3,
        "LastUpdate": last_update,
        "Revised": "",
        "Currency": "",
        "Unit": unit,
        "Ticker": "TEST",
        "Symbol": "TEST",
    }


def _cpi_rows():
    return [
        _row(calendar_id=1, event="Inflation Rate MoM", forecast="0.3%", forecast_value=0.3),
        _row(calendar_id=2, event="Core Inflation Rate MoM", forecast="0.2%", forecast_value=0.2),
    ]


def _employment_rows():
    return [
        _row(calendar_id=10, event="Non Farm Payrolls", forecast="45K", forecast_value=45000, unit="K"),
        _row(calendar_id=11, event="Unemployment Rate", forecast="4.2%", forecast_value=4.2),
        _row(calendar_id=12, event="Average Hourly Earnings MoM", forecast="0.2%", forecast_value=0.2),
    ]


def _cpi_target():
    return TradingEconomicsConsensusTarget(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        reference_period="2026-08",
        expected_release_at=RELEASE,
    )


def _employment_target():
    return TradingEconomicsConsensusTarget(
        event_key="BLS:EMPLOYMENT_SITUATION:2026-08",
        event_type="EMPLOYMENT_SITUATION",
        reference_period="2026-08",
        expected_release_at=RELEASE,
    )


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.payload)


class TradingEconomicsMacroConsensusProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = TradingEconomicsMacroConsensusProvider()

    def test_cpi_bundle_uses_only_representative_economist_forecast(self):
        snapshot = self.provider.parse_consensus(_cpi_rows(), target=_cpi_target(), first_seen_at=SEEN)
        self.assertEqual(snapshot.values, {"headline_mom_pct": 0.3, "core_mom_pct": 0.2})
        self.assertEqual(snapshot.units, {"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"})
        self.assertEqual(snapshot.provider_time, datetime(2026, 9, 11, 11, 59, tzinfo=timezone.utc))
        self.assertEqual(snapshot.source_name, "TRADING_ECONOMICS")
        self.assertNotIn("999", str(snapshot.values))
        self.assertNotIn("api_key", snapshot.source_ref.lower())

    def test_employment_bundle_normalizes_k_to_thousand_persons(self):
        snapshot = self.provider.parse_consensus(_employment_rows(), target=_employment_target(), first_seen_at=SEEN)
        self.assertEqual(snapshot.values["payroll_change_k"], 45.0)
        self.assertEqual(snapshot.values["unemployment_rate_pct"], 4.2)
        self.assertEqual(snapshot.values["avg_hourly_earnings_mom_pct"], 0.2)
        self.assertEqual(snapshot.units["payroll_change_k"], "THOUSAND_PERSONS")

    def test_teforecast_never_substitutes_for_missing_consensus(self):
        rows = _cpi_rows()
        rows[0]["Forecast"] = ""
        rows[0]["ForecastValue"] = None
        rows[0]["TEForecast"] = "0.9%"
        rows[0]["TEForecastValue"] = 0.9
        with self.assertRaisesRegex(ValueError, "Forecast is missing"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_post_release_actual_is_rejected(self):
        rows = _cpi_rows()
        rows[0]["Actual"] = "0.4%"
        rows[0]["ActualValue"] = 0.4
        with self.assertRaisesRegex(ValueError, "already contains Actual"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_estimated_event_time_is_rejected(self):
        rows = _cpi_rows()
        rows[0]["DateSpan"] = "1"
        with self.assertRaisesRegex(ValueError, "DateSpan=0"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_wrong_release_timestamp_is_rejected(self):
        rows = _cpi_rows()
        rows[1]["Date"] = "2026-09-11T13:30:00"
        with self.assertRaisesRegex(ValueError, "release time"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_wrong_reference_month_is_rejected(self):
        rows = _cpi_rows()
        rows[1]["ReferenceDate"] = "2026-07-31T00:00:00"
        with self.assertRaisesRegex(ValueError, "reference period"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_non_bls_source_is_rejected(self):
        rows = _cpi_rows()
        rows[0]["Source"] = "Other"
        rows[0]["SourceURL"] = "https://example.com"
        with self.assertRaisesRegex(ValueError, "Bureau of Labor Statistics"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_provider_last_update_after_first_seen_is_rejected(self):
        rows = _cpi_rows()
        rows[0]["LastUpdate"] = "2026-09-11T12:01:00"
        with self.assertRaisesRegex(ValueError, "after AlphaPilot first_seen_at"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_consensus_first_seen_at_or_after_release_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "strictly before official release"):
            self.provider.parse_consensus(_cpi_rows(), target=_cpi_target(), first_seen_at=RELEASE)

    def test_duplicate_matching_component_is_rejected(self):
        rows = _cpi_rows() + [_row(calendar_id=3, event="Inflation Rate MoM", forecast="0.4%", forecast_value=0.4)]
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.provider.parse_consensus(rows, target=_cpi_target(), first_seen_at=SEEN)

    def test_forecast_and_numeric_value_must_agree(self):
        rows = _employment_rows()
        rows[0]["ForecastValue"] = 99000
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.provider.parse_consensus(rows, target=_employment_target(), first_seen_at=SEEN)

    def test_fetch_is_disabled_by_default_and_does_not_call_client(self):
        client = _FakeClient(_cpi_rows())
        provider = TradingEconomicsMacroConsensusProvider(client=client, clock=lambda: SEEN)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            provider.fetch_consensus(target=_cpi_target())
        self.assertEqual(client.calls, [])

    def test_enabled_fetch_uses_values_true_and_api_key_but_never_persists_key_in_source_ref(self):
        client = _FakeClient(_cpi_rows())
        provider = TradingEconomicsMacroConsensusProvider(
            TradingEconomicsConsensusPolicy(enabled=True, api_key="SECRET"),
            client=client,
            clock=lambda: SEEN,
        )
        snapshot = provider.fetch_consensus(target=_cpi_target())
        self.assertEqual(len(client.calls), 1)
        url, kwargs = client.calls[0]
        self.assertIn("calendar/country/united%20states/2026-09-11/2026-09-11", url)
        self.assertEqual(kwargs["params"]["c"], "SECRET")
        self.assertEqual(kwargs["params"]["values"], "true")
        self.assertNotIn("SECRET", snapshot.source_ref)

    def test_target_requires_official_identity_reference_and_aware_release(self):
        with self.assertRaises(ValueError):
            TradingEconomicsConsensusTarget(
                event_key="BLS:CPI:2026-07",
                event_type="CPI",
                reference_period="2026-08",
                expected_release_at=RELEASE,
            ).validated()
        with self.assertRaises(ValueError):
            TradingEconomicsConsensusTarget(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                reference_period="2026-08",
                expected_release_at=datetime(2026, 9, 11, 12, 30),
            ).validated()

    def test_architecture_keeps_consensus_separate_from_te_model_and_trades(self):
        contract = architecture_contract()
        self.assertTrue(contract["representative_economist_forecast_used"])
        self.assertFalse(contract["te_model_forecast_used"])
        self.assertTrue(contract["date_span_zero_required"])
        self.assertTrue(contract["actual_must_be_missing"])
        self.assertTrue(contract["alpha_first_seen_must_precede_release"])
        self.assertFalse(contract["historical_backfill_enabled_in_v1"])
        self.assertFalse(contract["numeric_surprise_generated"])
        self.assertFalse(contract["btc_direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
