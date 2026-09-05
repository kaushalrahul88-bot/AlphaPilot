import unittest
from datetime import datetime, timezone

from app.bls_exact_macro_release_provider import BlsExactMacroReleaseProvider
from app.crypto_macro_event_intelligence import compute_numeric_surprise
from app.tradingeconomics_macro_consensus_provider import (
    TradingEconomicsConsensusTarget,
    TradingEconomicsMacroConsensusProvider,
)


CPI_URL = "https://www.bls.gov/news.release/cpi.nr0.htm"
EMP_URL = "https://www.bls.gov/news.release/empsit.nr0.htm"


def _cpi_html():
    return """
    <html><body>
      <p>Transmission of material in this release is embargoed until 8:30 a.m. (ET) Wednesday, August 12, 2026</p>
      <h1>CONSUMER PRICE INDEX - JULY 2026</h1>
      <p>The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.1 percent on a seasonally adjusted basis in July.</p>
      <p>The index for all items less food and energy rose 0.2 percent over the month.</p>
    </body></html>
    """


def _employment_html():
    return """
    <html><body>
      <p>Transmission of material in this release is embargoed until 8:30 a.m. (ET) Friday, September 4, 2026</p>
      <h1>THE EMPLOYMENT SITUATION - AUGUST 2026</h1>
      <p>Total nonfarm payroll employment increased by 162,000 in August.</p>
      <p>The unemployment rate was unchanged at 4.1 percent.</p>
      <p>In August, average hourly earnings for all employees on private nonfarm payrolls rose by 10 cents, or 0.3 percent to $36.53.</p>
    </body></html>
    """


def _te_row(calendar_id, event, *, date, reference_date, forecast, forecast_value, unit):
    return {
        "CalendarId": str(calendar_id),
        "Date": date,
        "Country": "United States",
        "Category": event,
        "Event": event,
        "Reference": "",
        "ReferenceDate": reference_date,
        "Source": "U.S. Bureau of Labor Statistics",
        "SourceURL": "https://www.bls.gov/",
        "Actual": "",
        "ActualValue": None,
        "Previous": "",
        "Forecast": forecast,
        "ForecastValue": forecast_value,
        "TEForecast": "999",
        "TEForecastValue": 999,
        "URL": "/united-states/test",
        "DateSpan": "0",
        "Importance": 3,
        "LastUpdate": datetime.fromisoformat(date).replace(tzinfo=timezone.utc).replace(hour=11, minute=59).isoformat().replace("+00:00", ""),
        "Revised": "",
        "Currency": "",
        "Unit": unit,
        "Ticker": "TEST",
        "Symbol": "TEST",
    }


class BlsTradingEconomicsMacroIntegrationTests(unittest.TestCase):
    def test_cpi_consensus_and_official_release_join_without_unit_or_identity_translation(self):
        release_at = datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)
        target = TradingEconomicsConsensusTarget(
            event_key="BLS:CPI:2026-07",
            event_type="CPI",
            reference_period="2026-07",
            expected_release_at=release_at,
        )
        consensus = TradingEconomicsMacroConsensusProvider().parse_consensus(
            [
                _te_row(1, "Inflation Rate MoM", date="2026-08-12T12:30:00", reference_date="2026-07-31T00:00:00", forecast="0.2%", forecast_value=0.2, unit="%"),
                _te_row(2, "Core Inflation Rate MoM", date="2026-08-12T12:30:00", reference_date="2026-07-31T00:00:00", forecast="0.3%", forecast_value=0.3, unit="%"),
            ],
            target=target,
            first_seen_at=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        )
        release = BlsExactMacroReleaseProvider().parse_release(
            _cpi_html(),
            url=CPI_URL,
            event_type="CPI",
            first_seen_at=datetime(2026, 8, 12, 12, 30, 3, tzinfo=timezone.utc),
        )
        surprise = compute_numeric_surprise(release, consensus)
        self.assertEqual(surprise.event_key, "BLS:CPI:2026-07")
        self.assertEqual(surprise.units, {"core_mom_pct": "PERCENT", "headline_mom_pct": "PERCENT"})
        self.assertAlmostEqual(surprise.surprise["headline_mom_pct"], -0.1)
        self.assertAlmostEqual(surprise.surprise["core_mom_pct"], -0.1)
        self.assertEqual(surprise.direction, "UNKNOWN")
        self.assertFalse(surprise.standalone_direction_allowed)

    def test_employment_consensus_k_units_match_bls_thousand_persons_exactly(self):
        release_at = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
        target = TradingEconomicsConsensusTarget(
            event_key="BLS:EMPLOYMENT_SITUATION:2026-08",
            event_type="EMPLOYMENT_SITUATION",
            reference_period="2026-08",
            expected_release_at=release_at,
        )
        consensus = TradingEconomicsMacroConsensusProvider().parse_consensus(
            [
                _te_row(10, "Non Farm Payrolls", date="2026-09-04T12:30:00", reference_date="2026-08-31T00:00:00", forecast="45K", forecast_value=45000, unit="K"),
                _te_row(11, "Unemployment Rate", date="2026-09-04T12:30:00", reference_date="2026-08-31T00:00:00", forecast="4.2%", forecast_value=4.2, unit="%"),
                _te_row(12, "Average Hourly Earnings MoM", date="2026-09-04T12:30:00", reference_date="2026-08-31T00:00:00", forecast="0.2%", forecast_value=0.2, unit="%"),
            ],
            target=target,
            first_seen_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        )
        release = BlsExactMacroReleaseProvider().parse_release(
            _employment_html(),
            url=EMP_URL,
            event_type="EMPLOYMENT_SITUATION",
            first_seen_at=datetime(2026, 9, 4, 12, 30, 2, tzinfo=timezone.utc),
        )
        surprise = compute_numeric_surprise(release, consensus)
        self.assertEqual(consensus.values["payroll_change_k"], 45.0)
        self.assertEqual(release.units["payroll_change_k"], "THOUSAND_PERSONS")
        self.assertEqual(consensus.units["payroll_change_k"], "THOUSAND_PERSONS")
        self.assertAlmostEqual(surprise.surprise["payroll_change_k"], 117.0)
        self.assertAlmostEqual(surprise.surprise["unemployment_rate_pct"], -0.1)
        self.assertAlmostEqual(surprise.surprise["avg_hourly_earnings_mom_pct"], 0.1)
        self.assertEqual(surprise.direction, "UNKNOWN")

    def test_mismatched_official_release_time_fails_before_surprise(self):
        target = TradingEconomicsConsensusTarget(
            event_key="BLS:CPI:2026-07",
            event_type="CPI",
            reference_period="2026-07",
            expected_release_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        )
        consensus = TradingEconomicsMacroConsensusProvider().parse_consensus(
            [
                _te_row(1, "Inflation Rate MoM", date="2026-08-12T12:30:00", reference_date="2026-07-31T00:00:00", forecast="0.2%", forecast_value=0.2, unit="%"),
                _te_row(2, "Core Inflation Rate MoM", date="2026-08-12T12:30:00", reference_date="2026-07-31T00:00:00", forecast="0.3%", forecast_value=0.3, unit="%"),
            ],
            target=target,
            first_seen_at=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        )
        release = BlsExactMacroReleaseProvider().parse_release(
            _cpi_html().replace("August 12, 2026", "August 13, 2026"),
            url=CPI_URL,
            event_type="CPI",
            first_seen_at=datetime(2026, 8, 13, 12, 30, 3, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(ValueError, "release_at timestamps must match exactly"):
            compute_numeric_surprise(release, consensus)


if __name__ == "__main__":
    unittest.main()
