from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.fno_selected_contract_tape_v1 import architecture_contract, build_selected_observation

UTC = timezone.utc


def _episode():
    return {
        "episode_id": "fnoep-quote-test",
        "underlying_symbol": "RELIANCE",
        "expiry_date": "2026-09-29",
        "trading_symbol": "RELIANCE26SEP100CE",
        "strike": 100.0,
        "option_type": "CE",
    }


def _chain():
    return {
        "data": {
            "payload": {
                "underlying_ltp": 100.0,
                "strikes": {
                    "100": {
                        "CE": {
                            "trading_symbol": "RELIANCE26SEP100CE",
                            "ltp": 50.0,
                            "open_interest": 10,
                            "volume": 5,
                            "greeks": {"iv": 20.0, "delta": 0.5},
                        },
                        "PE": {},
                    }
                },
            }
        }
    }


class FnoSelectedContractQuoteV1Tests(unittest.TestCase):
    def test_groww_bid_and_offer_price_are_preserved(self):
        record = build_selected_observation(
            _episode(),
            _chain(),
            collected_at=datetime(2026, 9, 7, 5, 0, tzinfo=UTC),
            direct_quote={
                "status": "AVAILABLE",
                "payload": {
                    "last_price": 51.0,
                    "bid_price": 50.5,
                    "offer_price": 51.5,
                },
            },
        )
        self.assertEqual(record["ltp"], 51.0)
        self.assertEqual(record["best_bid"], 50.5)
        self.assertEqual(record["best_ask"], 51.5)
        self.assertTrue(record["bid_ask_available"])

    def test_market_depth_is_fallback_not_fabrication(self):
        record = build_selected_observation(
            _episode(),
            _chain(),
            collected_at=datetime(2026, 9, 7, 5, 5, tzinfo=UTC),
            direct_quote={
                "status": "AVAILABLE",
                "payload": {
                    "last_price": 52.0,
                    "depth": {
                        "buy": [{"price": 51.75, "quantity": 100}],
                        "sell": [{"price": 52.25, "quantity": 100}],
                    },
                },
            },
        )
        self.assertEqual(record["best_bid"], 51.75)
        self.assertEqual(record["best_ask"], 52.25)
        self.assertTrue(record["bid_ask_available"])
        self.assertTrue(record["payload"]["provider_bid_ask_not_fabricated"])

    def test_contract_declares_provider_throttle(self):
        contract = architecture_contract()
        self.assertTrue(contract["provider_throttle_respected_before_direct_quote"])
        self.assertIn("offer_price", contract["groww_quote_keys_supported"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
