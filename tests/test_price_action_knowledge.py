import unittest

from app.price_action_knowledge import (
    BOOK_KNOWLEDGE_REVISION,
    price_action_breakout_signal,
    price_action_snapshot,
)


def _row(index, open_price, high, low, close, volume):
    hour = 9 + (15 + index * 5) // 60
    minute = (15 + index * 5) % 60
    return [f"2026-07-01T{hour:02d}:{minute:02d}:00+05:30", open_price, high, low, close, volume]


class PriceActionKnowledgeTests(unittest.TestCase):
    def test_confirmed_breakout_has_auditable_evidence(self):
        rows = []
        price = 100.0
        for index in range(24):
            offset = 0.15 if index % 2 else -0.15
            rows.append(_row(index, price, 100.6, 99.4, price + offset, 100.0))
        rows.append(_row(24, 100.4, 102.2, 100.3, 102.0, 180.0))

        snapshot = price_action_snapshot(rows, 24, "LONG", 1.0, 100.6)

        self.assertEqual(snapshot["knowledge_revision"], BOOK_KNOWLEDGE_REVISION)
        self.assertTrue(snapshot["breakout_close_confirmed"])
        self.assertEqual(snapshot["false_breakout_risk"], "LOW")
        self.assertIn(snapshot["price_action_grade"], {"ACCEPTABLE", "CONFIRMED"})
        self.assertGreaterEqual(snapshot["quality_score"], 4)

    def test_weak_close_and_volume_raise_false_breakout_risk(self):
        rows = [_row(index, 100.0, 100.5, 99.5, 100.0, 100.0) for index in range(24)]
        rows.append(_row(24, 100.0, 101.2, 99.9, 100.55, 70.0))

        snapshot = price_action_snapshot(rows, 24, "LONG", 1.0, 100.5)

        self.assertEqual(snapshot["false_breakout_risk"], "HIGH")
        self.assertEqual(snapshot["price_action_grade"], "WEAK")

    def test_strategy_uses_no_future_candle(self):
        rows = [_row(index, 100.0, 100.5, 99.5, 100.0, 100.0) for index in range(24)]
        rows.append(_row(24, 100.4, 102.2, 100.3, 102.0, 180.0))
        atrs = [1.0] * len(rows)

        signal = price_action_breakout_signal(rows, list(range(len(rows))), atrs)
        rows.append(_row(25, 102.0, 110.0, 90.0, 91.0, 500.0))
        repeated = price_action_breakout_signal(rows, list(range(len(rows) - 1)), atrs)

        self.assertIsNotNone(signal)
        self.assertEqual(signal, repeated)


if __name__ == "__main__":
    unittest.main()
