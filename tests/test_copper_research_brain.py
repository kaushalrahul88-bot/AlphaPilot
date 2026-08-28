from app.copper_research_brain import build_copper_experiences, build_copper_snapshot, brain_a_signal, attribute_brain_a_edges, brain_b_signal, compare_brains_a_b, evaluate_brain_a, evaluate_brain_b, experiment_manifest, label_forward_path


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
