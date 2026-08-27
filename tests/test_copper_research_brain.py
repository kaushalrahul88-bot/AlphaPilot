from app.copper_research_brain import build_copper_experiences, build_copper_snapshot, experiment_manifest, label_forward_path


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
