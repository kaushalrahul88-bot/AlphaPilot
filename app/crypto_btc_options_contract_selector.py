"""BTC Options contract selection brain, research/shadow only.

This module lives strictly inside the OPTIONS route. It consumes an already
approved BTC Options preflight plus point-in-time option-contract snapshots and
returns either one research candidate or NO OPTIONS CONTRACT. It never invokes
Futures, creates an order, chooses leverage, sizes capital, or commits funds.

The selector is intentionally deterministic and outcome-blind. Thresholds are
policy inputs, not fitted from later trade outcomes. CoinDCX is the default
platform, but live contract/fee metadata must be supplied by the caller rather
than hard-coded here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import log
from typing import Literal

OptionType = Literal["CALL", "PUT"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class BtcOptionContractSnapshot:
    symbol: str
    option_type: OptionType
    strike: float
    expiry_at: datetime
    observed_at: datetime
    bid: float
    ask: float
    mark: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    implied_volatility: float | None
    open_interest: float | None
    volume_24h: float | None
    source: str = "COINDCX_OPTIONS"
    platform: str = "COINDCX"
    underlying: str = "BTC"


@dataclass(frozen=True)
class BtcOptionsSelectionPolicy:
    max_quote_age_seconds: int = 120
    max_spread_pct: float = 8.0
    min_abs_delta: float = 0.20
    max_abs_delta: float = 0.85
    preferred_abs_delta: float = 0.50
    min_open_interest: float = 1.0
    min_volume_24h: float = 1.0
    min_expiry_buffer_hours: float = 2.0
    min_expiry_holding_multiple: float = 1.50
    preferred_expiry_holding_multiple: float = 3.0
    max_iv_percentile_for_full_score: float = 0.90


@dataclass(frozen=True)
class ContractEvaluation:
    symbol: str
    eligible: bool
    rejection_reasons: tuple[str, ...]
    score: float
    metrics: dict


def _midpoint(snapshot: BtcOptionContractSnapshot) -> float | None:
    if snapshot.bid > 0 and snapshot.ask > 0 and snapshot.ask >= snapshot.bid:
        return (snapshot.bid + snapshot.ask) / 2.0
    return None


def _reference_premium(snapshot: BtcOptionContractSnapshot) -> float | None:
    if snapshot.mark is not None and snapshot.mark > 0:
        return float(snapshot.mark)
    return _midpoint(snapshot)


def _expiry_fit_score(*, expiry_hours: float, holding_hours: float, preferred_multiple: float) -> float:
    ideal = max(holding_hours * preferred_multiple, holding_hours + 2.0)
    if expiry_hours <= 0 or ideal <= 0:
        return 0.0
    # Symmetric log-distance keeps very long expiries from winning merely for
    # having more time while avoiding a fragile exact-expiry preference.
    distance = abs(log(expiry_hours / ideal))
    return _bounded(1.0 - distance / 2.5)


def _delta_fit_score(abs_delta: float, preferred: float) -> float:
    return _bounded(1.0 - abs(abs_delta - preferred) / max(preferred, 1.0 - preferred, 0.01))


def evaluate_contract(
    snapshot: BtcOptionContractSnapshot,
    *,
    required_option_type: OptionType,
    decision_at: datetime,
    btc_spot_price: float,
    expected_move_pct: float,
    expected_holding_hours: float,
    fee_bps_per_side: float,
    iv_percentile: float | None,
    policy: BtcOptionsSelectionPolicy,
) -> ContractEvaluation:
    decision = _utc(decision_at)
    observed = _utc(snapshot.observed_at)
    expiry = _utc(snapshot.expiry_at)
    rejection: list[str] = []

    if str(snapshot.platform).upper() != "COINDCX":
        rejection.append("NON_DEFAULT_PLATFORM")
    if str(snapshot.underlying).upper() != "BTC":
        rejection.append("WRONG_UNDERLYING")
    if str(snapshot.option_type).upper() != required_option_type:
        rejection.append("WRONG_OPTION_SIDE")
    if observed > decision:
        rejection.append("FUTURE_QUOTE")

    quote_age_seconds = max(0.0, (decision - observed).total_seconds())
    if quote_age_seconds > policy.max_quote_age_seconds:
        rejection.append("STALE_QUOTE")

    expiry_hours = (expiry - decision).total_seconds() / 3600.0
    required_expiry_hours = max(
        policy.min_expiry_buffer_hours,
        expected_holding_hours * policy.min_expiry_holding_multiple,
    )
    if expiry_hours <= required_expiry_hours:
        rejection.append("EXPIRY_TOO_CLOSE_FOR_HORIZON")

    if btc_spot_price <= 0:
        rejection.append("INVALID_SPOT_PRICE")
    if snapshot.strike <= 0:
        rejection.append("INVALID_STRIKE")
    if snapshot.bid <= 0 or snapshot.ask <= 0 or snapshot.ask < snapshot.bid:
        rejection.append("INVALID_QUOTE")

    midpoint = _midpoint(snapshot)
    premium = _reference_premium(snapshot)
    if midpoint is None or premium is None or premium <= 0:
        rejection.append("PREMIUM_UNAVAILABLE")
        spread_pct = None
    else:
        spread_pct = ((snapshot.ask - snapshot.bid) / midpoint) * 100.0
        if spread_pct > policy.max_spread_pct:
            rejection.append("SPREAD_TOO_WIDE")

    if snapshot.delta is None:
        rejection.append("DELTA_MISSING")
        abs_delta = None
    else:
        abs_delta = abs(float(snapshot.delta))
        if required_option_type == "CALL" and snapshot.delta <= 0:
            rejection.append("DELTA_SIGN_MISMATCH")
        if required_option_type == "PUT" and snapshot.delta >= 0:
            rejection.append("DELTA_SIGN_MISMATCH")
        if not (policy.min_abs_delta <= abs_delta <= policy.max_abs_delta):
            rejection.append("DELTA_OUTSIDE_POLICY")

    if snapshot.implied_volatility is None or snapshot.implied_volatility <= 0:
        rejection.append("IV_MISSING")
    if snapshot.gamma is None:
        rejection.append("GAMMA_MISSING")
    if snapshot.theta is None:
        rejection.append("THETA_MISSING")
    if snapshot.vega is None:
        rejection.append("VEGA_MISSING")

    oi = None if snapshot.open_interest is None else float(snapshot.open_interest)
    volume = None if snapshot.volume_24h is None else float(snapshot.volume_24h)
    if oi is None or oi < policy.min_open_interest:
        rejection.append("OPEN_INTEREST_INSUFFICIENT")
    if volume is None or volume < policy.min_volume_24h:
        rejection.append("VOLUME_INSUFFICIENT")

    if expected_move_pct <= 0:
        rejection.append("EXPECTED_MOVE_INVALID")
    if expected_holding_hours <= 0:
        rejection.append("HOLDING_HORIZON_INVALID")
    if fee_bps_per_side < 0:
        rejection.append("FEE_INPUT_INVALID")

    moneyness_pct = None
    first_order_premium_response_pct = None
    if btc_spot_price > 0 and snapshot.strike > 0:
        moneyness_pct = ((snapshot.strike - btc_spot_price) / btc_spot_price) * 100.0
    if premium and premium > 0 and abs_delta is not None and btc_spot_price > 0 and expected_move_pct > 0:
        expected_underlying_move = btc_spot_price * expected_move_pct / 100.0
        # Diagnostic only: this is a first-order delta approximation and is
        # deliberately not labelled a premium forecast.
        first_order_premium_response_pct = (abs_delta * expected_underlying_move / premium) * 100.0

    roundtrip_cost_pct = None
    if spread_pct is not None and fee_bps_per_side >= 0:
        roundtrip_cost_pct = spread_pct + (2.0 * fee_bps_per_side / 100.0)

    eligible = not rejection
    score = 0.0
    component_scores: dict[str, float] = {}
    if eligible:
        delta_score = _delta_fit_score(abs_delta or 0.0, policy.preferred_abs_delta)
        spread_score = _bounded(1.0 - (spread_pct or 0.0) / max(policy.max_spread_pct, 0.01))
        expiry_score = _expiry_fit_score(
            expiry_hours=expiry_hours,
            holding_hours=expected_holding_hours,
            preferred_multiple=policy.preferred_expiry_holding_multiple,
        )
        liquidity_score = min(
            1.0,
            0.5 * min((oi or 0.0) / max(policy.min_open_interest * 10.0, 1.0), 1.0)
            + 0.5 * min((volume or 0.0) / max(policy.min_volume_24h * 10.0, 1.0), 1.0),
        )
        cost_score = 1.0 if roundtrip_cost_pct is None else _bounded(1.0 - roundtrip_cost_pct / 10.0)
        iv_score = 1.0
        if iv_percentile is not None:
            iv_p = _bounded(iv_percentile)
            if iv_p > policy.max_iv_percentile_for_full_score:
                iv_score = _bounded(
                    1.0 - (iv_p - policy.max_iv_percentile_for_full_score)
                    / max(1.0 - policy.max_iv_percentile_for_full_score, 0.01)
                )

        component_scores = {
            "delta_fit": delta_score,
            "spread_quality": spread_score,
            "expiry_fit": expiry_score,
            "liquidity": liquidity_score,
            "cost_quality": cost_score,
            "iv_regime": iv_score,
        }
        score = round(
            100.0
            * (
                0.25 * delta_score
                + 0.20 * spread_score
                + 0.20 * expiry_score
                + 0.15 * liquidity_score
                + 0.10 * cost_score
                + 0.10 * iv_score
            ),
            6,
        )

    return ContractEvaluation(
        symbol=snapshot.symbol,
        eligible=eligible,
        rejection_reasons=tuple(sorted(set(rejection))),
        score=score,
        metrics={
            "platform": str(snapshot.platform).upper(),
            "source": snapshot.source,
            "option_type": str(snapshot.option_type).upper(),
            "strike": snapshot.strike,
            "expiry_at": expiry.isoformat(),
            "observed_at": observed.isoformat(),
            "quote_age_seconds": quote_age_seconds,
            "expiry_hours": expiry_hours,
            "required_expiry_hours": required_expiry_hours,
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "reference_premium": premium,
            "spread_pct": spread_pct,
            "delta": snapshot.delta,
            "gamma": snapshot.gamma,
            "theta": snapshot.theta,
            "vega": snapshot.vega,
            "implied_volatility": snapshot.implied_volatility,
            "open_interest": oi,
            "volume_24h": volume,
            "moneyness_pct": moneyness_pct,
            "expected_move_pct": expected_move_pct,
            "expected_holding_hours": expected_holding_hours,
            "first_order_premium_response_pct_diagnostic": first_order_premium_response_pct,
            "estimated_roundtrip_cost_pct": roundtrip_cost_pct,
            "component_scores": component_scores,
        },
    )


def select_btc_option_contract(
    *,
    options_preflight: dict,
    contracts: list[BtcOptionContractSnapshot],
    decision_at: datetime,
    btc_spot_price: float,
    expected_move_pct: float,
    expected_holding_hours: float,
    fee_bps_per_side: float,
    iv_percentile: float | None = None,
    policy: BtcOptionsSelectionPolicy | None = None,
) -> dict:
    """Select one BTC Call/Put research candidate or fail closed.

    The function never creates a trade intent. Selection is a research
    candidate for a later Options risk/execution layer.
    """
    if str(options_preflight.get("instrument_type", "")).upper() != "OPTIONS":
        raise ValueError("BTC contract selector accepts OPTIONS preflight only")
    if options_preflight.get("futures_route_invoked") is True or options_preflight.get("futures_trade_generated") is True:
        raise ValueError("BTC Options selector rejects any Futures-route state")
    if options_preflight.get("trade_generated") is True:
        raise ValueError("BTC Options selector requires a pre-trade preflight")

    side = str(options_preflight.get("side_candidate", "NO_TRADE")).upper()
    if side == "NO_TRADE" or options_preflight.get("contract_selection_allowed") is not True:
        return {
            "version": "BTC_OPTIONS_CONTRACT_SELECTOR_V1",
            "asset": "BTC",
            "platform": "COINDCX",
            "instrument_type": "OPTIONS",
            "status": "NO_OPTIONS_CONTRACT",
            "side_candidate": side,
            "selected_contract": None,
            "reason": "Options preflight did not authorize contract selection.",
            "trade_generated": False,
            "futures_route_invoked": False,
            "futures_trade_generated": False,
            "broker_execution_enabled": False,
            "capital_committed": 0,
        }
    if side not in {"BUY_CALL", "BUY_PUT"}:
        raise ValueError(f"unsupported Options side candidate: {side}")

    required_option_type: OptionType = "CALL" if side == "BUY_CALL" else "PUT"
    policy = policy or BtcOptionsSelectionPolicy()
    evaluations = [
        evaluate_contract(
            row,
            required_option_type=required_option_type,
            decision_at=decision_at,
            btc_spot_price=btc_spot_price,
            expected_move_pct=expected_move_pct,
            expected_holding_hours=expected_holding_hours,
            fee_bps_per_side=fee_bps_per_side,
            iv_percentile=iv_percentile,
            policy=policy,
        )
        for row in contracts
    ]
    eligible = [row for row in evaluations if row.eligible]

    if not eligible:
        return {
            "version": "BTC_OPTIONS_CONTRACT_SELECTOR_V1",
            "asset": "BTC",
            "platform": "COINDCX",
            "instrument_type": "OPTIONS",
            "status": "NO_OPTIONS_CONTRACT",
            "side_candidate": side,
            "selected_contract": None,
            "reason": "No point-in-time BTC option contract passed the frozen selection policy.",
            "evaluated_contracts": [asdict(row) for row in evaluations],
            "trade_generated": False,
            "futures_route_invoked": False,
            "futures_trade_generated": False,
            "broker_execution_enabled": False,
            "capital_committed": 0,
        }

    # Deterministic tie-break: score, then tighter spread, then higher OI,
    # then earlier expiry, then symbol. No outcome data enters the ranking.
    def rank_key(row: ContractEvaluation):
        metrics = row.metrics
        spread = metrics.get("spread_pct")
        oi = metrics.get("open_interest")
        expiry_at = metrics.get("expiry_at")
        return (
            -row.score,
            float("inf") if spread is None else spread,
            -(0.0 if oi is None else oi),
            expiry_at or "",
            row.symbol,
        )

    selected = sorted(eligible, key=rank_key)[0]
    return {
        "version": "BTC_OPTIONS_CONTRACT_SELECTOR_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "OPTIONS_CONTRACT_CANDIDATE_SELECTED",
        "side_candidate": side,
        "selected_contract": asdict(selected),
        "eligible_contract_count": len(eligible),
        "evaluated_contract_count": len(evaluations),
        "policy": asdict(policy),
        "expected_move_pct": expected_move_pct,
        "expected_holding_hours": expected_holding_hours,
        "fee_bps_per_side": fee_bps_per_side,
        "iv_percentile": iv_percentile,
        "trade_generated": False,
        "quantity_selected": False,
        "order_created": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_CONTRACT_SELECTOR_CONTRACT_V1",
        "default_platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "requires_options_preflight": True,
        "requires_point_in_time_quotes": True,
        "requires_strike_and_expiry_from_real_contract_snapshot": True,
        "requires_liquidity_and_spread_checks": True,
        "requires_iv_and_greeks": True,
        "uses_expected_move_and_holding_horizon": True,
        "uses_live_fee_input_not_hardcoded_fee": True,
        "capital_rule_defined_here": False,
        "quantity_selected_here": False,
        "underlying_direction_created_here": False,
        "futures_route_invoked": False,
        "futures_fallback_allowed": False,
        "futures_leverage_allowed": False,
        "trade_generated_here": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
        "outcome_blind_ranking": True,
    }
