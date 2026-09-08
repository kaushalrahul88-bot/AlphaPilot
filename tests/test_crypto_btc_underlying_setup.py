from datetime import datetime, timedelta, timezone
import math

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow, HistoricalProvenance
from app.crypto_btc_underlying_setup import (
    BtcUnderlyingSetupPolicy,
    build_btc_underlying_setup,
    resolve_btc_underlying_setup,
)

UTC = timezone.utc
CLICK = datetime(2026, 9, 5, 12, tzinfo=UTC)
PROVENANCE = HistoricalProvenance(
    provider="TEST", source_id="BTC-15M", availability_basis="BAR_COMPLETION_RECONSTRUCTION",
    point_in_time_proven=True, immutable_archive=False, reconstructible_public_data=True,
)


def candle(available_at, low, high, close=None):
    close = (low + high) / 2 if close is None else close
    return BtcSpotCandleArchiveRow(
        open_at=available_at - timedelta(minutes=15), close_at=available_at, available_at=available_at,
        open=close, high=high, low=low, close=close, volume=10, provenance=PROVENANCE,
    )


def history():
    return [candle(CLICK - timedelta(minutes=15 * (8 - i)), 99 + i, 101 + i) for i in range(8)]


def market(direction="BULLISH"):
    return {"instrument_neutral": True, "direction": direction, "state": "COHERENT_DIRECTION_THESIS"}


def _assert_value_error(call, expected_text):
    try:
        call()
    except ValueError as exc:
        assert expected_text in str(exc)
    else:
        raise AssertionError(f"expected ValueError containing {expected_text!r}")


def test_unknown_direction_is_wait_and_options_cannot_create_direction():
    result = build_btc_underlying_setup(
        click_id="c", decision_at=CLICK, market_state=market("UNKNOWN"), completed_candles=history(),
        valid_until=CLICK + timedelta(hours=12),
    )
    assert result["decision"] == "WAIT"
    assert result["options_used_for_direction"] is False


def test_future_input_candle_is_rejected():
    _assert_value_error(
        lambda: build_btc_underlying_setup(
            click_id="c", decision_at=CLICK, market_state=market(),
            completed_candles=history() + [candle(CLICK + timedelta(minutes=15), 100, 101)],
            valid_until=CLICK + timedelta(hours=12),
        ),
        "unavailable",
    )


def test_geometry_has_minimum_one_point_five_r_and_no_execution():
    setup = build_btc_underlying_setup(
        click_id="c", decision_at=CLICK, market_state=market(), completed_candles=history(),
        valid_until=CLICK + timedelta(hours=12),
    )
    assert setup["decision"] == "BULLISH"
    assert setup["target1_r"] == 1.5
    expected_target = setup["entry_trigger"] + 1.5 * setup["risk_per_btc"]
    assert math.isclose(setup["target1"], expected_target, rel_tol=1e-12, abs_tol=1e-12)
    assert setup["live_execution"] is False
    assert setup["futures_trade_generated"] is False


def test_policy_rejects_reward_below_one_point_five_r():
    _assert_value_error(
        lambda: BtcUnderlyingSetupPolicy(minimum_risk_reward=1.4).validated(),
        ">= 1.5",
    )


def test_actual_path_resolves_target_and_same_bar_collision_is_ambiguous():
    setup = build_btc_underlying_setup(
        click_id="c", decision_at=CLICK, market_state=market(), completed_candles=history(),
        valid_until=CLICK + timedelta(hours=12),
    )
    entry, stop, t1 = setup["entry_trigger"], setup["stop_loss"], setup["target1"]
    entered = candle(CLICK + timedelta(minutes=15), entry - 0.1, entry + 0.1)
    target = candle(CLICK + timedelta(minutes=30), entry, t1 + 0.1)
    assert resolve_btc_underlying_setup(setup=setup, future_candles=[entered, target])["status"] == "T1"
    ambiguous = candle(CLICK + timedelta(minutes=15), stop - 0.1, entry + 0.1)
    assert resolve_btc_underlying_setup(setup=setup, future_candles=[ambiguous])["status"] == "AMBIGUOUS_ENTRY_BAR"


def test_preentry_invalidation_cancels_and_absent_trigger_is_no_entry():
    setup = build_btc_underlying_setup(
        click_id="c", decision_at=CLICK, market_state=market(), completed_candles=history(),
        valid_until=CLICK + timedelta(hours=12),
    )
    stop = setup["stop_loss"]
    assert resolve_btc_underlying_setup(
        setup=setup, future_candles=[candle(CLICK + timedelta(minutes=15), stop - 0.1, stop + 0.1)]
    )["status"] == "CANCELLED"
    assert resolve_btc_underlying_setup(
        setup=setup, future_candles=[candle(CLICK + timedelta(minutes=15), 100, 101)]
    )["status"] == "NO_ENTRY"
