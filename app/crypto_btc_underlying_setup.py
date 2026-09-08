"""Deterministic, underlying-only BTC setup geometry and path replay.

This module deliberately stops before Options selection.  A point-in-time BTC
market state may authorize a directional setup; completed spot candles then
define its trigger and invalidation.  A later, separately supplied candle path
resolves the frozen setup without a fixed forecast horizon hidden in the model.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class BtcUnderlyingSetupPolicy:
    structure_bars: int = 8
    entry_buffer_fraction: float = 0.05
    stop_buffer_fraction: float = 0.10
    minimum_risk_reward: float = 1.5
    target2_r: float = 2.0

    def validated(self) -> "BtcUnderlyingSetupPolicy":
        if self.structure_bars < 3:
            raise ValueError("structure_bars must be >= 3")
        if self.entry_buffer_fraction < 0 or self.stop_buffer_fraction < 0:
            raise ValueError("buffer fractions must be >= 0")
        if self.minimum_risk_reward < 1.5:
            raise ValueError("minimum_risk_reward must be >= 1.5")
        if self.target2_r < self.minimum_risk_reward:
            raise ValueError("target2_r cannot be below target1 R")
        return self


def _true_range(rows: list[BtcSpotCandleArchiveRow]) -> float:
    ranges: list[float] = []
    prior_close: float | None = None
    for row in rows:
        values = [float(row.high) - float(row.low)]
        if prior_close is not None:
            values.extend((abs(float(row.high) - prior_close), abs(float(row.low) - prior_close)))
        ranges.append(max(values))
        prior_close = float(row.close)
    return sum(ranges) / len(ranges)


def build_btc_underlying_setup(
    *,
    click_id: str,
    decision_at: datetime,
    market_state: dict,
    completed_candles: list[BtcSpotCandleArchiveRow],
    valid_until: datetime,
    policy: BtcUnderlyingSetupPolicy | None = None,
) -> dict:
    """Freeze BULLISH/BEARISH geometry, or WAIT, using only visible candles."""
    policy = (policy or BtcUnderlyingSetupPolicy()).validated()
    decision, end = _utc(decision_at), _utc(valid_until)
    if end <= decision:
        raise ValueError("valid_until must be after decision_at")
    if str(market_state.get("instrument_neutral", "")).lower() not in {"true", "1"}:
        raise ValueError("market_state must be instrument-neutral")
    direction = str(market_state.get("direction", "UNKNOWN")).upper()
    rows = sorted(completed_candles, key=lambda row: _utc(row.available_at))
    for row in rows:
        row.validated()
        if _utc(row.available_at) > decision:
            raise ValueError("setup input contains a candle unavailable at decision_at")

    common = {
        "version": "BTC_UNDERLYING_SETUP_V1",
        "click_id": str(click_id),
        "decision_at": decision.isoformat(),
        "valid_until": end.isoformat(),
        "asset": "BTC",
        "instrument": "UNDERLYING_ONLY",
        "research_only": True,
        "live_execution": False,
        "capital_committed_inr": 0,
        "options_used_for_direction": False,
        "futures_trade_generated": False,
    }
    if direction not in {"BULLISH", "BEARISH"}:
        return {**common, "decision": "WAIT", "reason": str(market_state.get("state") or "NO_COHERENT_DIRECTION")}
    if len(rows) < policy.structure_bars:
        return {**common, "decision": "WAIT", "reason": "INSUFFICIENT_COMPLETED_STRUCTURE_CANDLES"}

    window = rows[-policy.structure_bars :]
    atr = _true_range(window)
    if not isfinite(atr) or atr <= 0:
        return {**common, "decision": "WAIT", "reason": "INVALID_STRUCTURE_RANGE"}
    swing_high = max(float(row.high) for row in window)
    swing_low = min(float(row.low) for row in window)
    if direction == "BULLISH":
        entry = swing_high + atr * policy.entry_buffer_fraction
        stop = swing_low - atr * policy.stop_buffer_fraction
        risk = entry - stop
        target1 = entry + risk * policy.minimum_risk_reward
        target2 = entry + risk * policy.target2_r
    else:
        entry = swing_low - atr * policy.entry_buffer_fraction
        stop = swing_high + atr * policy.stop_buffer_fraction
        risk = stop - entry
        target1 = entry - risk * policy.minimum_risk_reward
        target2 = entry - risk * policy.target2_r
    if not isfinite(risk) or risk <= 0:
        return {**common, "decision": "WAIT", "reason": "INVALID_RISK_GEOMETRY"}

    return {
        **common,
        "decision": direction,
        "reason": "COHERENT_DIRECTION_WITH_STRUCTURAL_TRIGGER",
        "entry_trigger": round(entry, 8),
        "invalidation": round(stop, 8),
        "stop_loss": round(stop, 8),
        "target1": round(target1, 8),
        "target2": round(target2, 8),
        "risk_per_btc": round(risk, 8),
        "target1_r": policy.minimum_risk_reward,
        "target2_r": policy.target2_r,
        "structure": {
            "bars": policy.structure_bars,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "average_true_range": atr,
        },
        "policy": asdict(policy),
    }


def resolve_btc_underlying_setup(*, setup: dict, future_candles: list[BtcSpotCandleArchiveRow]) -> dict:
    """Resolve entry/stop/targets from the actual completed OHLC path.

    Same-candle ordering is unknowable from OHLC and is therefore AMBIGUOUS.
    T1 is the terminal planned target; T2 is reported when the target candle
    itself proves the farther extension was reached.
    """
    if setup.get("decision") == "WAIT":
        return {"status": "NO_SETUP", "r_multiple": None}
    direction = str(setup.get("decision", "")).upper()
    if direction not in {"BULLISH", "BEARISH"}:
        raise ValueError("setup decision must be BULLISH, BEARISH, or WAIT")
    decision = _utc(datetime.fromisoformat(str(setup["decision_at"])))
    end = _utc(datetime.fromisoformat(str(setup["valid_until"])))
    entry, stop = float(setup["entry_trigger"]), float(setup["stop_loss"])
    target1, target2 = float(setup["target1"]), float(setup["target2"])
    risk = float(setup["risk_per_btc"])
    rows = sorted(future_candles, key=lambda row: _utc(row.available_at))
    eligible = []
    for row in rows:
        row.validated()
        available = _utc(row.available_at)
        if available <= decision:
            raise ValueError("outcome path contains a candle visible at or before decision_at")
        if available <= end:
            eligible.append(row)

    entered_at: datetime | None = None
    mfe_r = 0.0
    mae_r = 0.0
    for row in eligible:
        stamp = _utc(row.available_at)
        hit_entry = float(row.high) >= entry if direction == "BULLISH" else float(row.low) <= entry
        hit_stop = float(row.low) <= stop if direction == "BULLISH" else float(row.high) >= stop
        hit_t1 = float(row.high) >= target1 if direction == "BULLISH" else float(row.low) <= target1
        hit_t2 = float(row.high) >= target2 if direction == "BULLISH" else float(row.low) <= target2
        if entered_at is None:
            if hit_entry and (hit_stop or hit_t1):
                return {"status": "AMBIGUOUS_ENTRY_BAR", "entry_at": stamp.isoformat(), "r_multiple": None}
            if hit_stop:
                return {"status": "CANCELLED", "cancelled_at": stamp.isoformat(), "r_multiple": None}
            if not hit_entry:
                continue
            entered_at = stamp

        if direction == "BULLISH":
            mfe_r = max(mfe_r, (float(row.high) - entry) / risk)
            mae_r = min(mae_r, (float(row.low) - entry) / risk)
        else:
            mfe_r = max(mfe_r, (entry - float(row.low)) / risk)
            mae_r = min(mae_r, (entry - float(row.high)) / risk)
        if hit_stop and hit_t1:
            return {"status": "AMBIGUOUS_STOP_TARGET_BAR", "entry_at": entered_at.isoformat(), "exit_at": stamp.isoformat(), "r_multiple": None, "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4)}
        if hit_stop:
            return {"status": "STOP", "entry_at": entered_at.isoformat(), "exit_at": stamp.isoformat(), "r_multiple": -1.0, "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4)}
        if hit_t1:
            achieved = float(setup["target2_r"]) if hit_t2 else float(setup["target1_r"])
            return {"status": "T2" if hit_t2 else "T1", "entry_at": entered_at.isoformat(), "exit_at": stamp.isoformat(), "r_multiple": achieved, "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4)}

    if entered_at is None:
        return {"status": "NO_ENTRY", "session_end": end.isoformat(), "r_multiple": None}
    return {"status": "SESSION_END", "entry_at": entered_at.isoformat(), "session_end": end.isoformat(), "r_multiple": None, "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4)}
