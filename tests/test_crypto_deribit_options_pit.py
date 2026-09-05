import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_deribit_options_pit import DATASET, architecture_contract, deribit_options_context_archive_record
from app.deribit_btc_options_context_provider import DeribitBtcOptionsContextCapture


def _t(minutes=0):
    return datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _capture(seen=None, iv=55.0):
    first_seen = seen or _t()
    return DeribitBtcOptionsContextCapture(
        first_seen_at=first_seen,
        underlying_price_usd=100_000.0,
        nearest_expiry_at=first_seen + timedelta(days=2),
        next_expiry_at=first_seen + timedelta(days=9),
        atm_mark_iv_pct=iv,
        next_expiry_atm_mark_iv_pct=iv + 3.0,
        term_structure_slope_iv_points=3.0,
        total_call_open_interest_btc=10.0,
        total_put_open_interest_btc=12.0,
        put_call_open_interest_ratio=1.2,
        matched_contract_count=20,
        active_contract_count=22,
        valid_expiry_count=2,
    ).validated()


class CryptoDeribitOptionsPitTests(unittest.TestCase):
    def test_snapshot_archives_as_global_context_not_coindcx_contract_data(self):
        record = deribit_options_context_archive_record(_capture())
        self.assertEqual(record.dataset, DATASET)
        self.assertEqual(record.provider, "DERIBIT_PUBLIC_API")
        self.assertIsNone(record.event_at)
        self.assertTrue(record.payload["global_options_context_only"])
        self.assertFalse(record.payload["coindcx_contract_data"])
        self.assertFalse(record.payload["coindcx_contract_selection_allowed"])
        self.assertFalse(record.payload["coindcx_quote_fill_allowed"])
        self.assertFalse(record.payload["coindcx_pnl_replay_allowed"])
        self.assertFalse(record.payload["underlying_direction_assigned"])
        self.assertFalse(record.payload["trade_generation_allowed"])
        self.assertIsNone(record.payload["skew_25d"])
        self.assertFalse(record.payload["skew_25d_inferred"])

    def test_first_seen_controls_historical_visibility(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(deribit_options_context_archive_record(_capture(seen=_t(5))))
        self.assertEqual(ledger.visible_as_of(_t(4), dataset=DATASET), [])
        self.assertEqual(len(ledger.visible_as_of(_t(5), dataset=DATASET)), 1)

    def test_current_chain_polls_are_distinct_first_seen_snapshots(self):
        ledger = ImmutableBtcPitLedger()
        first = deribit_options_context_archive_record(_capture(seen=_t(0), iv=55.0))
        second = deribit_options_context_archive_record(_capture(seen=_t(10), iv=57.0))
        self.assertNotEqual(first.source_key, second.source_key)
        ledger.insert_first_seen(first)
        ledger.insert_first_seen(second)
        rows = ledger.visible_as_of(_t(10), dataset=DATASET)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["payload"]["atm_mark_iv_pct"], 55.0)
        self.assertEqual(rows[1]["payload"]["atm_mark_iv_pct"], 57.0)

    def test_contract_keeps_deribit_out_of_coindcx_execution(self):
        contract = architecture_contract()
        self.assertFalse(contract["current_snapshot_is_backdated_history"])
        self.assertTrue(contract["first_seen_controls_click_visibility"])
        self.assertFalse(contract["skew_25d_inferred"])
        self.assertTrue(contract["global_options_context_only"])
        self.assertFalse(contract["coindcx_contract_selection_allowed"])
        self.assertFalse(contract["coindcx_quote_fill_allowed"])
        self.assertFalse(contract["coindcx_pnl_replay_allowed"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
