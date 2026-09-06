from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.delta_india_btc_derivatives_context import (
    DELTA_BTC_OI_SYMBOL,
    DeltaIndiaBtcDerivativesContextPolicy,
    DeltaIndiaBtcDerivativesPublicProvider,
    DeltaIndiaBtcOiCandle,
    architecture_contract,
    combine_delta_btc_live_positioning,
    derive_delta_live_positioning_evidence,
    derive_delta_oi_positioning_evidence,
    normalize_delta_btc_oi_candles,
    normalize_delta_btc_rest_ticker,
    normalize_delta_btc_ws_ticker,
)

UTC = timezone.utc


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *, params=None, timeout=None, headers=None):
        self.calls.append({
            "url": url,
            "params": None if params is None else dict(params),
            "timeout": timeout,
            "headers": dict(headers or {}),
        })
        return _Response(self.payload)


def _raw(open_at: datetime, oi: float) -> dict:
    return {
        "time": int(open_at.timestamp()),
        "open": oi,
        "high": oi * 1.01,
        "low": oi * 0.99,
        "close": oi,
        "volume": 0,
    }


def _candle(open_at: datetime, oi: float) -> DeltaIndiaBtcOiCandle:
    return DeltaIndiaBtcOiCandle(
        open_at=open_at,
        available_at=open_at + timedelta(minutes=5),
        resolution="5m",
        open=oi,
        high=oi * 1.01,
        low=oi * 0.99,
        close=oi,
        volume=0,
    ).validated()


def _rest_payload(at: datetime, *, oi_contracts: float = 100.0, oi_value_usd: float = 1_000_000.0) -> dict:
    return {
        "success": True,
        "result": {
            "symbol": "BTCUSD",
            "oi": oi_contracts,
            "oi_value_usd": oi_value_usd,
            "spot_price": 80_000.0,
            "timestamp": int(at.timestamp()),
        },
    }


def _ws_payload(at: datetime, *, oi_contracts: float = 100.5, oi_change_usd_6h: float = 10_000.0) -> dict:
    return {
        "type": "ticker",
        "ts": int(at.timestamp()),
        "sp": 80_000.0,
        "d": [{"s": "BTCUSD", "oi": [oi_contracts, oi_change_usd_6h]}],
    }


def _live_snapshot(*, oi_change_usd_6h: float = 10_000.0):
    rest_seen = datetime(2026, 9, 6, 10, 0, 1, tzinfo=UTC)
    ws_seen = datetime(2026, 9, 6, 10, 0, 2, tzinfo=UTC)
    rest = normalize_delta_btc_rest_ticker(_rest_payload(rest_seen), received_at=rest_seen)
    ws = normalize_delta_btc_ws_ticker(
        _ws_payload(ws_seen, oi_change_usd_6h=oi_change_usd_6h),
        received_at=ws_seen,
    )
    return combine_delta_btc_live_positioning(rest, ws)


class DeltaIndiaBtcDerivativesContextTests(unittest.TestCase):
    def test_provider_uses_documented_public_oi_symbol(self):
        start = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
        payload = {"success": True, "result": [_raw(start, 100.0), _raw(start + timedelta(minutes=5), 101.0)]}
        client = _Client(payload)
        provider = DeltaIndiaBtcDerivativesPublicProvider(
            policy=DeltaIndiaBtcDerivativesContextPolicy(enabled=True, resolution="5m"),
            client=client,
        )
        rows = provider.fetch_oi_candles(start_at=start, end_at=start + timedelta(minutes=10))
        self.assertEqual(len(rows), 2)
        self.assertEqual(client.calls[0]["params"]["symbol"], DELTA_BTC_OI_SYMBOL)
        self.assertEqual(client.calls[0]["params"]["resolution"], "5m")
        self.assertEqual(client.calls[0]["headers"], {"Accept": "application/json"})

    def test_normalization_uses_time_plus_resolution_as_availability(self):
        opened = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
        rows = normalize_delta_btc_oi_candles({"success": True, "result": [_raw(opened, 100.0)]}, resolution="5m")
        self.assertEqual(rows[0].open_at, opened)
        self.assertEqual(rows[0].available_at, opened + timedelta(minutes=5))

    def test_completed_oi_expansion_and_positive_price_is_bullish(self):
        decision = datetime(2026, 9, 6, 10, 11, tzinfo=UTC)
        rows = [
            _candle(datetime(2026, 9, 6, 9, 5, tzinfo=UTC), 100.0),
            _candle(datetime(2026, 9, 6, 10, 5, tzinfo=UTC), 101.0),
            _candle(datetime(2026, 9, 6, 10, 10, tzinfo=UTC), 102.0),
        ]
        evidence = derive_delta_oi_positioning_evidence(rows, decision_at=decision, price_change_pct=0.50)
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertFalse(evidence.context_only)
        self.assertEqual(evidence.causal_origin, "LEVERAGED_POSITIONING")
        self.assertEqual(evidence.metadata["latest_available_at"], "2026-09-06T10:10:00+00:00")

    def test_completed_oi_expansion_and_negative_price_is_bearish(self):
        decision = datetime(2026, 9, 6, 10, 11, tzinfo=UTC)
        rows = [
            _candle(datetime(2026, 9, 6, 9, 5, tzinfo=UTC), 100.0),
            _candle(datetime(2026, 9, 6, 10, 5, tzinfo=UTC), 101.0),
        ]
        evidence = derive_delta_oi_positioning_evidence(rows, decision_at=decision, price_change_pct=-0.50)
        self.assertEqual(evidence.stance, "BEARISH")
        self.assertFalse(evidence.context_only)

    def test_oi_contraction_stays_unknown_without_liquidation_side(self):
        decision = datetime(2026, 9, 6, 10, 11, tzinfo=UTC)
        rows = [
            _candle(datetime(2026, 9, 6, 9, 5, tzinfo=UTC), 101.0),
            _candle(datetime(2026, 9, 6, 10, 5, tzinfo=UTC), 100.0),
        ]
        evidence = derive_delta_oi_positioning_evidence(rows, decision_at=decision, price_change_pct=-0.75)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_incomplete_future_candle_is_not_used(self):
        decision = datetime(2026, 9, 6, 10, 11, tzinfo=UTC)
        rows = [
            _candle(datetime(2026, 9, 6, 9, 5, tzinfo=UTC), 100.0),
            _candle(datetime(2026, 9, 6, 10, 5, tzinfo=UTC), 101.0),
            _candle(datetime(2026, 9, 6, 10, 10, tzinfo=UTC), 150.0),
        ]
        evidence = derive_delta_oi_positioning_evidence(rows, decision_at=decision, price_change_pct=0.50)
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertEqual(evidence.metadata["latest_oi"], 101.0)
        self.assertEqual(evidence.metadata["latest_available_at"], "2026-09-06T10:10:00+00:00")

    def test_rest_and_websocket_normalizers_preserve_first_seen_semantics(self):
        rest_seen = datetime(2026, 9, 6, 10, 0, 1, tzinfo=UTC)
        ws_seen = datetime(2026, 9, 6, 10, 0, 2, tzinfo=UTC)
        rest = normalize_delta_btc_rest_ticker(_rest_payload(rest_seen), received_at=rest_seen)
        ws = normalize_delta_btc_ws_ticker(_ws_payload(ws_seen), received_at=ws_seen)
        snapshot = combine_delta_btc_live_positioning(rest, ws)
        self.assertEqual(snapshot.first_seen_at, ws_seen)
        self.assertEqual(snapshot.current_oi_value_usd, 1_000_000.0)
        self.assertEqual(snapshot.oi_change_usd_6h, 10_000.0)
        self.assertAlmostEqual(snapshot.oi_change_pct_6h, 10_000.0 / 990_000.0 * 100.0)
        self.assertEqual(snapshot.frozen_dict()["rolling_window_hours"], 6)

    def test_live_oi_expansion_and_positive_price_is_bullish(self):
        snapshot = _live_snapshot(oi_change_usd_6h=10_000.0)
        decision = snapshot.first_seen_at + timedelta(seconds=1)
        evidence = derive_delta_live_positioning_evidence(snapshot, decision_at=decision, price_change_pct_6h=1.0)
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertFalse(evidence.context_only)
        self.assertEqual(evidence.causal_origin, "LEVERAGED_POSITIONING")

    def test_live_oi_expansion_and_negative_price_is_bearish(self):
        snapshot = _live_snapshot(oi_change_usd_6h=10_000.0)
        decision = snapshot.first_seen_at + timedelta(seconds=1)
        evidence = derive_delta_live_positioning_evidence(snapshot, decision_at=decision, price_change_pct_6h=-1.0)
        self.assertEqual(evidence.stance, "BEARISH")
        self.assertFalse(evidence.context_only)

    def test_live_oi_contraction_is_unknown_without_liquidation_side(self):
        snapshot = _live_snapshot(oi_change_usd_6h=-10_000.0)
        decision = snapshot.first_seen_at + timedelta(seconds=1)
        evidence = derive_delta_live_positioning_evidence(snapshot, decision_at=decision, price_change_pct_6h=-1.0)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_live_positioning_snapshot_after_decision_is_rejected(self):
        snapshot = _live_snapshot()
        with self.assertRaisesRegex(ValueError, "after decision_at"):
            derive_delta_live_positioning_evidence(
                snapshot,
                decision_at=snapshot.first_seen_at - timedelta(microseconds=1),
                price_change_pct_6h=1.0,
            )

    def test_contract_keeps_futures_context_separate_from_trading(self):
        contract = architecture_contract()
        self.assertTrue(contract["historical_oi_supported_by_documentation"])
        self.assertEqual(contract["live_ticker_oi_change_window_hours"], 6)
        self.assertTrue(contract["live_snapshot_first_seen_required"])
        self.assertFalse(contract["oi_contraction_may_be_directional_without_liquidations"])
        self.assertTrue(contract["futures_context_may_inform_options"])
        self.assertFalse(contract["futures_trade_generation_allowed"])
        self.assertFalse(contract["options_quote_substitution_allowed"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
