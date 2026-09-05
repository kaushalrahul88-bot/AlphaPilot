"""BTC Options premium/exit geometry, research/shadow only.

Translates an already-selected BTC option contract plus an underlying BTC thesis
(invalidation, target and time horizon) into conservative *scenario estimates*
for option premium at stop/target. It does not predict future premium, place an
order, or invoke Futures.

Primary stop semantics remain UNDERLYING_INVALIDATION. Premium levels produced
here are translation references for risk sizing/replay, not guaranteed fills or
standalone execution triggers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite


@dataclass(frozen=True)
class BtcOptionsUnderlyingThesis:
    entry_btc_price: float
    invalidation_btc_price: float
    target_btc_price: float
    expected_holding_hours: float
    stop_time_hours: float
    target_time_hours: float
    stop_iv_change_points: float = 0.0
    target_iv_change_points: float = 0.0
    iv_stress_points: float = 5.0

    def validated(self, side_candidate: str) -> "BtcOptionsUnderlyingThesis":
        for name in (
            "entry_btc_price",
            "invalidation_btc_price",
            "target_btc_price",
            "expected_holding_hours",
            "stop_time_hours",
            "target_time_hours",
        ):
            value = float(getattr(self, name))
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        for name in ("stop_iv_change_points", "target_iv_change_points", "iv_stress_points"):
            value = float(getattr(self, name))
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.iv_stress_points < 0:
            raise ValueError("iv_stress_points must be >= 0")
        if self.stop_time_hours > self.expected_holding_hours:
            raise ValueError("stop_time_hours cannot exceed expected_holding_hours")
        if self.target_time_hours > self.expected_holding_hours:
            raise ValueError("target_time_hours cannot exceed expected_holding_hours")

        side = str(side_candidate).upper()
        if side == "BUY_CALL":
            if not (self.invalidation_btc_price < self.entry_btc_price < self.target_btc_price):
                raise ValueError("BUY_CALL thesis requires invalidation < entry < target")
        elif side == "BUY_PUT":
            if not (self.target_btc_price < self.entry_btc_price < self.invalidation_btc_price):
                raise ValueError("BUY_PUT thesis requires target < entry < invalidation")
        else:
            raise ValueError("premium geometry requires BUY_CALL or BUY_PUT")
        return self


@dataclass(frozen=True)
class BtcOptionsGreekConvention:
    """Explicit units to avoid silently mis-scaling theta/vega.

    delta: premium change per 1 unit BTC move
    gamma: delta change per 1 unit BTC move
    theta: premium change per day
    vega: premium change per 1 implied-volatility percentage point
    """

    theta_per_day: bool = True
    vega_per_iv_point: bool = True

    def validated(self) -> "BtcOptionsGreekConvention":
        if self.theta_per_day is not True:
            raise ValueError("V1 requires theta quoted per day")
        if self.vega_per_iv_point is not True:
            raise ValueError("V1 requires vega quoted per IV percentage point")
        return self


def _selected_metrics(contract_selection: dict) -> tuple[str, str, dict]:
    if str(contract_selection.get("instrument_type", "")).upper() != "OPTIONS":
        raise ValueError("premium geometry accepts OPTIONS contract selection only")
    if contract_selection.get("futures_route_invoked") is True or contract_selection.get("futures_trade_generated") is True:
        raise ValueError("premium geometry rejects Futures-route state")
    if contract_selection.get("status") != "OPTIONS_CONTRACT_CANDIDATE_SELECTED":
        raise ValueError("premium geometry requires a selected Options contract")
    selected = contract_selection.get("selected_contract") or {}
    if selected.get("eligible") is not True:
        raise ValueError("selected Options contract must be eligible")
    metrics = dict(selected.get("metrics") or {})
    side = str(contract_selection.get("side_candidate", "")).upper()
    option_type = str(metrics.get("option_type", "")).upper()
    if side == "BUY_CALL" and option_type != "CALL":
        raise ValueError("BUY_CALL selection must contain a CALL")
    if side == "BUY_PUT" and option_type != "PUT":
        raise ValueError("BUY_PUT selection must contain a PUT")
    return side, str(selected.get("symbol") or ""), metrics


def _finite_metric(metrics: dict, name: str) -> float:
    value = metrics.get(name)
    if value is None:
        raise ValueError(f"selected contract missing {name}")
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"selected contract {name} must be finite")
    return value


def _entry_premium(metrics: dict) -> float:
    # Long-option planning uses the ask as the conservative entry reference.
    ask = _finite_metric(metrics, "ask")
    if ask <= 0:
        raise ValueError("selected contract ask must be > 0")
    return ask


def _taylor_premium(
    *,
    entry_premium: float,
    delta: float,
    gamma: float,
    theta_per_day: float,
    vega_per_iv_point: float,
    btc_move: float,
    elapsed_hours: float,
    iv_change_points: float,
) -> dict:
    delta_component = delta * btc_move
    gamma_component = 0.5 * gamma * btc_move * btc_move
    theta_component = theta_per_day * (elapsed_hours / 24.0)
    vega_component = vega_per_iv_point * iv_change_points
    raw = entry_premium + delta_component + gamma_component + theta_component + vega_component
    return {
        "premium": max(0.0, float(raw)),
        "raw_premium": float(raw),
        "delta_component": float(delta_component),
        "gamma_component": float(gamma_component),
        "theta_component": float(theta_component),
        "vega_component": float(vega_component),
    }


def _scenario_band(
    *,
    entry_premium: float,
    delta: float,
    gamma: float,
    theta: float,
    vega: float,
    btc_move: float,
    elapsed_hours: float,
    assumed_iv_change_points: float,
    iv_stress_points: float,
) -> dict:
    center = _taylor_premium(
        entry_premium=entry_premium,
        delta=delta,
        gamma=gamma,
        theta_per_day=theta,
        vega_per_iv_point=vega,
        btc_move=btc_move,
        elapsed_hours=elapsed_hours,
        iv_change_points=assumed_iv_change_points,
    )
    lower_iv = _taylor_premium(
        entry_premium=entry_premium,
        delta=delta,
        gamma=gamma,
        theta_per_day=theta,
        vega_per_iv_point=vega,
        btc_move=btc_move,
        elapsed_hours=elapsed_hours,
        iv_change_points=assumed_iv_change_points - iv_stress_points,
    )
    higher_iv = _taylor_premium(
        entry_premium=entry_premium,
        delta=delta,
        gamma=gamma,
        theta_per_day=theta,
        vega_per_iv_point=vega,
        btc_move=btc_move,
        elapsed_hours=elapsed_hours,
        iv_change_points=assumed_iv_change_points + iv_stress_points,
    )
    premiums = [center["premium"], lower_iv["premium"], higher_iv["premium"]]
    return {
        "assumed": center,
        "lower_iv_stress": lower_iv,
        "higher_iv_stress": higher_iv,
        "conservative_premium": min(premiums),
        "optimistic_premium": max(premiums),
        "iv_stress_points": iv_stress_points,
    }


def build_btc_options_exit_geometry(
    *,
    contract_selection: dict,
    thesis: BtcOptionsUnderlyingThesis,
    greek_convention: BtcOptionsGreekConvention | None = None,
) -> dict:
    """Translate BTC invalidation/target into conservative premium scenarios."""
    side, symbol, metrics = _selected_metrics(contract_selection)
    thesis = thesis.validated(side)
    (greek_convention or BtcOptionsGreekConvention()).validated()

    entry = _entry_premium(metrics)
    delta = _finite_metric(metrics, "delta")
    gamma = _finite_metric(metrics, "gamma")
    theta = _finite_metric(metrics, "theta")
    vega = _finite_metric(metrics, "vega")
    iv = _finite_metric(metrics, "implied_volatility")
    if iv <= 0:
        raise ValueError("selected contract implied_volatility must be > 0")
    if gamma < 0:
        raise ValueError("long vanilla option gamma must be >= 0")
    if vega < 0:
        raise ValueError("long vanilla option vega must be >= 0")
    if side == "BUY_CALL" and delta <= 0:
        raise ValueError("BUY_CALL requires positive delta")
    if side == "BUY_PUT" and delta >= 0:
        raise ValueError("BUY_PUT requires negative delta")

    stop_move = thesis.invalidation_btc_price - thesis.entry_btc_price
    target_move = thesis.target_btc_price - thesis.entry_btc_price

    stop_band = _scenario_band(
        entry_premium=entry,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        btc_move=stop_move,
        elapsed_hours=thesis.stop_time_hours,
        assumed_iv_change_points=thesis.stop_iv_change_points,
        iv_stress_points=thesis.iv_stress_points,
    )
    target_band = _scenario_band(
        entry_premium=entry,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        btc_move=target_move,
        elapsed_hours=thesis.target_time_hours,
        assumed_iv_change_points=thesis.target_iv_change_points,
        iv_stress_points=thesis.iv_stress_points,
    )

    # Conservative values are suitable for downstream risk geometry. They are
    # scenario estimates only; live/replay exit still keys off the BTC thesis
    # invalidation/target and actual observed option quote.
    stop_reference = float(stop_band["conservative_premium"])
    target_reference = float(target_band["conservative_premium"])
    if stop_reference >= entry:
        raise ValueError("translated conservative stop premium must remain below entry premium")
    if target_reference <= entry:
        raise ValueError("translated conservative target premium must remain above entry premium")

    underlying_stop_distance_pct = abs(stop_move) / thesis.entry_btc_price * 100.0
    underlying_target_distance_pct = abs(target_move) / thesis.entry_btc_price * 100.0
    local_approximation_warning = max(underlying_stop_distance_pct, underlying_target_distance_pct) > 5.0

    return {
        "version": "BTC_OPTIONS_EXIT_GEOMETRY_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "side_candidate": side,
        "symbol": symbol,
        "status": "OPTIONS_EXIT_GEOMETRY_READY",
        "entry_premium_reference": entry,
        "primary_stop_basis": "UNDERLYING_INVALIDATION",
        "primary_target_basis": "UNDERLYING_TARGET",
        "time_exit_hours": thesis.expected_holding_hours,
        "invalidation_btc_price": thesis.invalidation_btc_price,
        "target_btc_price": thesis.target_btc_price,
        "stop_premium_reference": stop_reference,
        "target_premium_reference": target_reference,
        "stop_band": stop_band,
        "target_band": target_band,
        "thesis": asdict(thesis),
        "greek_convention": asdict(greek_convention or BtcOptionsGreekConvention()),
        "model": "SECOND_ORDER_GREEK_TAYLOR_SCENARIO",
        "premium_projection_is_forecast": False,
        "premium_reference_is_guaranteed_fill": False,
        "premium_stop_is_primary_execution_trigger": False,
        "actual_quote_required_at_exit": True,
        "local_approximation_warning": local_approximation_warning,
        "underlying_stop_distance_pct": underlying_stop_distance_pct,
        "underlying_target_distance_pct": underlying_target_distance_pct,
        "risk_scenario": {
            "stop_premium": stop_reference,
            "target_premium": target_reference,
            "stop_basis": "BTC_THESIS_INVALIDATION_TRANSLATED_CONSERVATIVELY",
            "target_basis": "BTC_EXPECTED_MOVE_TRANSLATED_CONSERVATIVELY",
        },
        "trade_generated": False,
        "order_created": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_EXIT_GEOMETRY_CONTRACT_V1",
        "instrument_type": "OPTIONS",
        "requires_selected_options_contract": True,
        "requires_underlying_invalidation_and_target": True,
        "primary_stop_basis": "UNDERLYING_INVALIDATION",
        "premium_stop_is_arbitrary_percent": False,
        "premium_projection_is_forecast": False,
        "premium_reference_is_guaranteed_fill": False,
        "actual_quote_required_at_exit": True,
        "iv_uncertainty_stressed": True,
        "theta_and_vega_units_explicit": True,
        "futures_route_invoked": False,
        "futures_fallback_allowed": False,
        "trade_generated_here": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }
