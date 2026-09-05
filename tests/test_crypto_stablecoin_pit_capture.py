import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_stablecoin_pit_capture import (
    STABLECOIN_SUPPLY_DATASET,
    architecture_contract,
    defillama_stablecoin_archive_record,
)
from app.defillama_stablecoin_provider import DefiLlamaStablecoinSupplyCapture


def _t(minute=30):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


def _capture(first_seen=None, total=150.0):
    usdt = total * (2.0 / 3.0)
    usdc = total - usdt
    return DefiLlamaStablecoinSupplyCapture(
        first_seen_at=first_seen or _t(),
        peg_type="peggedUSD",
        total_circulating=total,
        by_symbol={"USDC": usdc, "USDT": usdt},
        prices={"USDC": 1.0, "USDT": 1.0},
        asset_count=2,
    ).validated()


class CryptoStablecoinPitCaptureTests(unittest.TestCase):
    def test_capture_archives_raw_aggregate_supply_without_direction(self):
        record = defillama_stablecoin_archive_record(_capture())
        self.assertEqual(record.dataset, STABLECOIN_SUPPLY_DATASET)
        self.assertEqual(record.provider, "DEFILLAMA_STABLECOINS")
        self.assertEqual(record.first_seen_at, _t())
        self.assertIsNone(record.event_at)
        self.assertEqual(record.payload["total_circulating"], 150.0)
        self.assertFalse(record.payload["aggregate_supply_equals_exchange_inflow"])
        self.assertFalse(record.payload["aggregate_supply_equals_deployable_spot_buying_power"])
        self.assertFalse(record.payload["standalone_direction_assigned"])

    def test_first_seen_controls_historical_visibility_when_provider_event_time_is_absent(self):
        ledger = ImmutableBtcPitLedger()
        record = defillama_stablecoin_archive_record(_capture(first_seen=_t()))
        inserted = ledger.insert_first_seen(record)
        self.assertEqual(inserted["status"], "INSERTED_FIRST_SEEN")
        before = ledger.visible_as_of(_t() - timedelta(seconds=1), dataset=STABLECOIN_SUPPLY_DATASET)
        after = ledger.visible_as_of(_t(), dataset=STABLECOIN_SUPPLY_DATASET)
        self.assertEqual(before, [])
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["first_seen_at"], _t().isoformat())

    def test_separate_polls_are_separate_first_seen_observations_without_fake_provider_time(self):
        first = defillama_stablecoin_archive_record(_capture(first_seen=_t(), total=150.0))
        second = defillama_stablecoin_archive_record(_capture(first_seen=_t(40), total=151.0))
        self.assertNotEqual(first.source_key, second.source_key)
        self.assertIsNone(first.event_at)
        self.assertIsNone(second.event_at)
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(first)
        ledger.insert_first_seen(second)
        rows = ledger.visible_as_of(_t(40), dataset=STABLECOIN_SUPPLY_DATASET)
        self.assertEqual(len(rows), 2)

    def test_contract_refuses_backdating_or_exchange_flow_equivalence(self):
        contract = architecture_contract()
        self.assertTrue(contract["first_seen_controls_click_visibility"])
        self.assertFalse(contract["historical_values_backdated_to_click"])
        self.assertFalse(contract["aggregate_supply_equals_exchange_inflow"])
        self.assertFalse(contract["aggregate_supply_equals_deployable_spot_buying_power"])
        self.assertFalse(contract["standalone_direction_assigned"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
