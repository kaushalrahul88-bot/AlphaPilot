import unittest
from datetime import datetime, timezone

from app.tradingeconomics_macro_consensus_provider import TradingEconomicsConsensusTarget
from app.tradingeconomics_macro_historical_pit_audit import (
    architecture_contract,
    audit_historical_point_in_time_rows,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
TARGET = TradingEconomicsConsensusTarget(
    event_key="BLS:CPI:2026-08",
    event_type="CPI",
    reference_period="2026-08",
    expected_release_at=RELEASE,
)


def _row(*, calendar_id, event, forecast, actual="0.2%", teforecast="0.3%", datespan="0", last_update="2026-09-11T12:30:05"):
    return {
        "CalendarId": calendar_id,
        "Date": "2026-09-11T12:30:00",
        "Country": "United States",
        "Category": event,
        "Event": event,
        "Reference": "Aug",
        "ReferenceDate": "2026-08-31T00:00:00",
        "Source": "U.S. Bureau of Labor Statistics",
        "SourceURL": "https://www.bls.gov",
        "Actual": actual,
        "Forecast": forecast,
        "TEForecast": teforecast,
        "DateSpan": datespan,
        "LastUpdate": last_update,
        "Unit": "%",
    }


def _bundle(**overrides):
    headline = _row(calendar_id="CPI-H", event="Inflation Rate MoM", forecast="0.3%")
    core = _row(calendar_id="CPI-C", event="Core Inflation Rate MoM", forecast="0.3%")
    if "headline" in overrides:
        headline.update(overrides["headline"])
    if "core" in overrides:
        core.update(overrides["core"])
    return [headline, core]


class TradingEconomicsHistoricalPitAuditTests(unittest.TestCase):
    def test_historical_event_values_are_verified_without_inventing_first_seen(self):
        audit = audit_historical_point_in_time_rows(_bundle(), target=TARGET)
        self.assertEqual(audit.status, "PIT_VALUE_VERIFIED_FIRST_SEEN_UNPROVEN")
        self.assertEqual(audit.values, {"headline_mom_pct": 0.3, "core_mom_pct": 0.3})
        self.assertEqual(audit.units, {"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"})
        self.assertTrue(audit.provider_point_in_time_value_verified)
        self.assertFalse(audit.exact_pre_release_first_seen_proven)
        self.assertFalse(audit.macro_consensus_snapshot_may_be_constructed)
        self.assertFalse(audit.exact_numeric_surprise_engine_admission)
        self.assertFalse(audit.synthetic_first_seen_assigned)
        self.assertFalse(audit.teforecast_used)
        self.assertTrue(all(ts >= RELEASE for ts in audit.provider_row_last_updates))

    def test_historical_actual_is_allowed_but_never_used_as_consensus_provenance(self):
        audit = audit_historical_point_in_time_rows(
            _bundle(headline={"Actual": "0.4%"}, core={"Actual": "0.3%"}),
            target=TARGET,
        )
        self.assertEqual(audit.values["headline_mom_pct"], 0.3)
        self.assertEqual(audit.values["core_mom_pct"], 0.3)
        self.assertFalse(audit.exact_pre_release_first_seen_proven)

    def test_teforecast_never_substitutes_for_missing_representative_consensus(self):
        rows = _bundle(headline={"Forecast": "", "TEForecast": "0.8%"})
        with self.assertRaisesRegex(ValueError, "Forecast is missing"):
            audit_historical_point_in_time_rows(rows, target=TARGET)

    def test_estimated_event_timing_is_rejected(self):
        rows = _bundle(core={"DateSpan": "1"})
        with self.assertRaisesRegex(ValueError, "DateSpan=0"):
            audit_historical_point_in_time_rows(rows, target=TARGET)

    def test_wrong_reference_period_or_release_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "reference period"):
            audit_historical_point_in_time_rows(
                _bundle(core={"ReferenceDate": "2026-07-31T00:00:00"}),
                target=TARGET,
            )
        with self.assertRaisesRegex(ValueError, "release time"):
            audit_historical_point_in_time_rows(
                _bundle(core={"Date": "2026-09-11T12:31:00"}),
                target=TARGET,
            )

    def test_architecture_keeps_historical_backfill_out_of_strict_surprise_engine(self):
        contract = architecture_contract()
        self.assertTrue(contract["provider_point_in_time_calendar_documented_for_backtesting"])
        self.assertTrue(contract["representative_economist_forecast_field_used"])
        self.assertFalse(contract["teforecast_used"])
        self.assertTrue(contract["provider_last_update_may_be_at_or_after_release"])
        self.assertFalse(contract["provider_last_update_treated_as_consensus_first_seen"])
        self.assertFalse(contract["synthetic_pre_release_first_seen_allowed"])
        self.assertFalse(contract["macro_consensus_snapshot_constructed"])
        self.assertFalse(contract["exact_numeric_surprise_engine_admission"])
        self.assertTrue(contract["prospective_first_seen_capture_remains_authoritative"])
        self.assertFalse(contract["historical_backfill_auto_enabled"])
        self.assertFalse(contract["btc_direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
