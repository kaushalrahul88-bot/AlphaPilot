import unittest

from app.setup_discovery_v3 import run_setup_discovery_v3


class EmptyProvider:
    async def historical_candles(self, symbol, timeframe, start, end):
        return []


class SetupDiscoveryV3Tests(unittest.IsolatedAsyncioTestCase):
    async def test_v3_protocol_metadata_is_frozen(self):
        result = await run_setup_discovery_v3(
            EmptyProvider(), ["RELIANCE"], "2026-04-13", "2026-04-17"
        )
        self.assertEqual(result["mode"], "ALPHAPILOT_SETUP_DISCOVERY_V3_FAST_FOLLOW_THROUGH")
        self.assertFalse(result["production_rules_changed"])
        self.assertEqual(result["fixed_gates"]["replication_blocks_required"], 4)
        self.assertEqual(len(result["rows"]), 8)

    async def test_v3_rejects_dates_outside_frozen_development_book(self):
        with self.assertRaisesRegex(ValueError, "frozen"):
            await run_setup_discovery_v3(
                EmptyProvider(), ["RELIANCE"], "2026-08-11", "2026-08-17"
            )

    async def test_v3_rejects_blocks_longer_than_one_week(self):
        with self.assertRaisesRegex(ValueError, "7 calendar days"):
            await run_setup_discovery_v3(
                EmptyProvider(), ["RELIANCE"], "2026-04-13", "2026-04-21"
            )
