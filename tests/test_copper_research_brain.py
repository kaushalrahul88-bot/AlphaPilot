import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.copper_research_brain import build_copper_experiences, build_copper_snapshot, brain_a_signal, attribute_brain_a_edges, regime_stability_study, run_copper_regime_stability_from_store, brain_b_signal, compare_brains_a_b, evaluate_brain_a, evaluate_brain_b, experiment_manifest, label_forward_path


def _rows(n=90, start=800.0):
    rows = []
    price = start
    for i in range(n):
        price += 0.5
        rows.append([f"2026-01-02T{9 + (i * 5)//60:02d}:{(i * 5)%60:02d}:00+05:30", price - .2, price + .4, price - .5, price, 100 + i, 1000 + i * 2])
    return rows


def test_snapshot_is_timestamp_aligned_and_uses_oi_volume():
    rows = _rows()
    snap = build_copper_snapshot(rows, 55, lme_candles=rows, comex_candles=rows, usdinr_candles=rows)
    assert snap["structure"] == "UPTREND"
    assert snap["relative_volume"] > 1
    assert snap["oi_change_15m_pct"] > 0
    assert snap["lme_return_15m_pct"] > 0


def test_forward_labels_are_separate_from_features():
    rows = _rows()
    labels = label_forward_path(rows, 55)
    assert labels["forward_15m_pct"] > 0
    assert labels["mfe_60m_pct"] > 0
    assert labels["mae_60m_pct"] > -1


def test_experience_builder_reserves_forward_horizon():
    rows = _rows(100)
    experiences = build_copper_experiences(rows, sample_every_bars=5)
    assert experiences
    assert all("features" in x and "labels" in x for x in experiences)
    assert all("forward_120m_pct" in x["labels"] for x in experiences)


def test_manifest_keeps_research_out_of_production():
    manifest = experiment_manifest()
    assert manifest["research_only"] is True
    assert manifest["production_rules_changed"] is False
    assert manifest["promotion_order"][-1] == "live_eligible"


def test_asof_context_does_not_require_exact_timestamp():
    rows = _rows()
    context = [row for i, row in enumerate(rows) if i % 2 == 0]
    snap = build_copper_snapshot(rows, 55, comex_candles=context)
    assert snap["comex_return_15m_pct"] is not None


def test_brain_a_is_technical_only_and_evaluable():
    rows = _rows(120)
    experiences = build_copper_experiences(rows, sample_every_bars=3)
    assert brain_a_signal(experiences[0]["features"]) in {"BUY", "SELL", "NO_TRADE"}
    report = evaluate_brain_a(experiences, horizon_minutes=60, round_trip_cost_bps=4)
    assert report["brain"] == "A"
    assert report["research_only"] is True
    assert report["round_trip_cost_bps"] == 4.0


def test_brain_b_filters_weak_participation():
    features = {
        "structure": "UPTREND", "return_15m_pct": 0.1,
        "ema20_gap_pct": 0.1, "ema50_gap_pct": 0.2,
        "relative_volume": 0.5, "atr_pct": 0.2, "oi_change_15m_pct": 0.1,
    }
    assert brain_a_signal(features) == "BUY"
    assert brain_b_signal(features) == "NO_TRADE"


def test_brain_b_comparison_uses_chronological_holdout():
    rows = _rows(180)
    experiences = build_copper_experiences(rows, sample_every_bars=2)
    report = compare_brains_a_b(experiences, train_fraction=0.70)
    assert report["split"]["train_experiences"] > report["split"]["holdout_experiences"]
    assert report["holdout"]["brain_a"]["brain"] == "A"
    assert report["holdout"]["brain_b"]["brain"] == "B"
    assert "brain_b_promoted" in report["gate"]


def test_edge_attribution_is_descriptive_only():
    rows = _rows(180)
    experiences = build_copper_experiences(rows, sample_every_bars=2)
    report = attribute_brain_a_edges(experiences)
    assert report["mode"] == "DESCRIPTIVE_EDGE_ATTRIBUTION"
    assert report["threshold_optimization"] is False
    assert report["observations"] > 0
    assert "session" in report["dimensions"]
    assert "volume_bucket" in report["dimensions"]
    assert "oi_bucket" in report["dimensions"]


def test_regime_stability_splits_chronologically():
    rows = _rows(260)
    experiences = build_copper_experiences(rows, sample_every_bars=2)
    study = regime_stability_study(experiences, windows=4)
    assert study["mode"] == "COPPER_REGIME_STABILITY_STUDY"
    assert study["threshold_optimization"] is False
    assert study["windows"] == 4
    assert sum(study["window_sizes"]) == len(experiences)
    assert "session" in study["stability"]
    assert isinstance(study["recurring_positive_candidates"], list)


def test_experience_builder_matches_public_snapshot_and_labels():
    rows = _rows(140)
    experiences = build_copper_experiences(rows, sample_every_bars=3)
    first_index = 50
    assert experiences[0]["features"] == build_copper_snapshot(rows, first_index)
    assert experiences[0]["labels"] == label_forward_path(rows, first_index)


class _SegmentStore:
    async def initialize(self):
        return None

    async def read_symbol_contract_segments(self, symbol, timeframe_minutes, start, end):
        a = _rows(150, start=800.0)
        b = _rows(150, start=1200.0)
        return [
            {"trading_symbol":"COPPERA","expiry_date":"2026-01-31","candles":a},
            {"trading_symbol":"COPPERB","expiry_date":"2026-02-28","candles":b},
        ]


async def _run_segmented_store():
    return await run_copper_regime_stability_from_store(
        _SegmentStore(), days=45, sample_every_bars=3, round_trip_cost_bps=4.0, windows=4,
    )


def test_stored_stability_never_crosses_contract_boundaries():
    import asyncio
    result = asyncio.run(_run_segmented_store())
    assert result["rollover_guard"] == "EXPERIENCES_NEVER_CROSS_CONTRACT_BOUNDARIES"
    assert result["coverage"]["contracts"] == 2
    expected = sum(len(build_copper_experiences(segment, sample_every_bars=3)) for segment in (_rows(150,800.0), _rows(150,1200.0)))
    assert result["coverage"]["experiences"] == expected


class CopperInformationQualityV2Tests(unittest.TestCase):
    def test_snapshot_adds_session_location_without_future_data(self):
        rows = []
        base = datetime(2026, 8, 28, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        for i in range(70):
            price = 900.0 + i * 0.25
            rows.append([
                (base + timedelta(minutes=5*i)).isoformat(),
                price - 0.1, price + 0.4, price - 0.3, price,
                1000 + i * 10, 50000 + i * 5,
            ])
        snapshot = build_copper_snapshot(rows, 60)
        self.assertIn("session_range_position", snapshot)
        self.assertIn("session_vwap_gap_pct", snapshot)
        self.assertIn("opening_range_break", snapshot)
        self.assertIn("time_adjusted_relative_volume", snapshot)
        self.assertIn("price_oi_state", snapshot)
        self.assertEqual(snapshot["opening_range_break"], "ABOVE")
        self.assertEqual(snapshot["price_oi_state"], "LONG_BUILDUP")

    def test_snapshot_is_invariant_to_future_candle_mutation(self):
        rows = []
        base = datetime(2026, 8, 28, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        for i in range(80):
            price = 900.0 + i * 0.1
            rows.append([(base + timedelta(minutes=5*i)).isoformat(), price, price+0.2, price-0.2, price, 1000, 50000])
        before = build_copper_snapshot(rows, 60)
        rows[70][2] = 9999.0
        rows[70][5] = 99999999
        after = build_copper_snapshot(rows, 60)
        self.assertEqual(before, after)
