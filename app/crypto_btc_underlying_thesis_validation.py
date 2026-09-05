"""Point-in-time BTC underlying-thesis validation for Crypto Brain research.

This module deliberately stops *before* Options translation. It asks a narrower
question than the full Options replay runner: did the BTC Market Brain's frozen
BULLISH / BEARISH / UNKNOWN thesis have directional value over a predefined
future horizon?

Decision inputs are restricted to evidence visible by the click. Future BTC
prices are attached only after the decision fingerprint is frozen. No option
contract, premium, Greeks, execution metadata, Futures route, P&L, or capital is
required or generated here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from json import dumps
from math import isfinite
from typing import Iterable

from app.crypto_btc_historical_data_adapter import (
    BtcHistoricalArchive,
    forward_btc_prices,
    latest_spot_candle,
    visible_evidence_at,
)
from app.crypto_btc_information_board import build_btc_information_board
from app.crypto_btc_random_click_experience import (
    BtcForwardPriceObservation,
    BtcRandomClickPolicy,
    generate_random_clicks,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite(name: str, value: float, *, nonnegative: bool = False, positive: bool = False) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be >= 0")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


@dataclass(frozen=True)
class BtcUnderlyingThesisValidationPolicy:
    click_policy: BtcRandomClickPolicy
    trade_horizon: str
    max_spot_age_seconds: int
    evaluation_horizon_hours: float
    terminal_price_max_gap_seconds: int
    neutral_band_pct: float
    large_move_threshold_pct: float

    def validated(self) -> "BtcUnderlyingThesisValidationPolicy":
        self.click_policy.validated()
        if self.trade_horizon not in {"scalp", "intraday", "swing", "position"}:
            raise ValueError("unsupported trade_horizon")
        if int(self.max_spot_age_seconds) < 0:
            raise ValueError("max_spot_age_seconds must be >= 0")
        _finite("evaluation_horizon_hours", self.evaluation_horizon_hours, positive=True)
        if int(self.terminal_price_max_gap_seconds) < 0:
            raise ValueError("terminal_price_max_gap_seconds must be >= 0")
        neutral = _finite("neutral_band_pct", self.neutral_band_pct, nonnegative=True)
        large = _finite("large_move_threshold_pct", self.large_move_threshold_pct, positive=True)
        if large <= neutral:
            raise ValueError("large_move_threshold_pct must be greater than neutral_band_pct")
        return self


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _utc(value).isoformat()


def _decision_fingerprint(payload: dict) -> str:
    encoded = dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


def _decision_snapshot(*, click_id: str, decision_at: datetime, btc_price: float, board: dict, evidence: list) -> dict:
    state = board["underlying_market_state"]
    direction = str(state.get("direction", "UNKNOWN")).upper()
    counted = list(state.get("counted_evidence") or [])
    counted_origins = sorted({str(row.get("causal_origin")) for row in counted if row.get("causal_origin")})
    counted_families = sorted({str(row.get("family")) for row in counted if row.get("family")})
    latest_evidence = None
    if evidence:
        latest_evidence = max(_utc(row.observed_at) for row in evidence)
        if latest_evidence > _utc(decision_at):
            raise ValueError("decision evidence cannot be first seen after the click")

    lane_status = board.get("lane_status") or {}
    available_lanes = sorted(lane for lane, row in lane_status.items() if row.get("available") is True)
    missing_lanes = sorted(board.get("missing_lanes") or [])
    payload = {
        "version": "BTC_UNDERLYING_THESIS_DECISION_V1",
        "click_id": click_id,
        "decision_at": _utc(decision_at).isoformat(),
        "decision_btc_price": float(btc_price),
        "market_direction": direction,
        "market_state": state.get("state"),
        "underlying_thesis_available": direction in {"BULLISH", "BEARISH"},
        "counted_causal_origins": counted_origins,
        "counted_families": counted_families,
        "available_lanes": available_lanes,
        "missing_lanes": missing_lanes,
        "latest_evidence_at": _iso(latest_evidence),
        "counted_evidence_count": len(counted),
        "future_prices_in_decision": False,
        "options_contract_data_used": False,
        "options_execution_metadata_used": False,
        "options_trade_generated": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
    }
    return {**payload, "decision_fingerprint": _decision_fingerprint(payload)}


def _validate_forward_rows(
    rows: Iterable[BtcForwardPriceObservation],
    *,
    decision_at: datetime,
    horizon_end: datetime,
) -> list[BtcForwardPriceObservation]:
    decision = _utc(decision_at)
    end = _utc(horizon_end)
    result: list[BtcForwardPriceObservation] = []
    previous = None
    for row in rows:
        row.validated()
        observed = _utc(row.observed_at)
        if observed <= decision:
            raise ValueError("outcome price must be strictly after the decision")
        if observed > end:
            continue
        if previous is not None and observed < previous:
            raise ValueError("outcome prices must be chronological")
        previous = observed
        result.append(row)
    return result


def _evaluate_outcome(
    *,
    decision_at: datetime,
    entry_price: float,
    market_direction: str,
    forward_prices: list[BtcForwardPriceObservation],
    policy: BtcUnderlyingThesisValidationPolicy,
) -> dict:
    decision = _utc(decision_at)
    horizon_end = decision + timedelta(hours=float(policy.evaluation_horizon_hours))
    rows = _validate_forward_rows(forward_prices, decision_at=decision, horizon_end=horizon_end)
    if not rows:
        return {
            "status": "OUTCOME_UNRESOLVED",
            "reason": "NO_FORWARD_BTC_PRICE_IN_EVALUATION_HORIZON",
            "performance_eligible": False,
            "outcome_used_for_decision": False,
        }

    terminal = rows[-1]
    terminal_at = _utc(terminal.observed_at)
    terminal_gap = (horizon_end - terminal_at).total_seconds()
    if terminal_gap < 0 or terminal_gap > int(policy.terminal_price_max_gap_seconds):
        return {
            "status": "OUTCOME_UNRESOLVED",
            "reason": "TERMINAL_BTC_PRICE_TOO_FAR_FROM_HORIZON_END",
            "terminal_price_at": terminal_at.isoformat(),
            "terminal_gap_seconds": terminal_gap,
            "performance_eligible": False,
            "outcome_used_for_decision": False,
        }

    entry = _finite("entry_price", entry_price, positive=True)
    returns = [((float(row.btc_price) - entry) / entry * 100.0, _utc(row.observed_at)) for row in rows]
    terminal_return = (float(terminal.btc_price) - entry) / entry * 100.0
    max_up_pct, max_up_at = max(returns, key=lambda item: item[0])
    max_down_pct, max_down_at = min(returns, key=lambda item: item[0])
    max_abs_move_pct = max(abs(max_up_pct), abs(max_down_pct))

    neutral = float(policy.neutral_band_pct)
    if terminal_return > neutral:
        realized_direction = "UP"
    elif terminal_return < -neutral:
        realized_direction = "DOWN"
    else:
        realized_direction = "FLAT"

    direction = str(market_direction or "UNKNOWN").upper()
    if direction not in {"BULLISH", "BEARISH"}:
        classification = "ABSTENTION_RESOLVED"
        performance_eligible = False
        hit = None
    elif realized_direction == "FLAT":
        classification = "DIRECTIONAL_INCONCLUSIVE"
        performance_eligible = False
        hit = None
    else:
        expected = "UP" if direction == "BULLISH" else "DOWN"
        hit = realized_direction == expected
        classification = "DIRECTIONAL_HIT" if hit else "DIRECTIONAL_MISS"
        performance_eligible = True

    favorable_excursion = None
    adverse_excursion = None
    if direction == "BULLISH":
        favorable_excursion = max_up_pct
        adverse_excursion = abs(min(0.0, max_down_pct))
    elif direction == "BEARISH":
        favorable_excursion = abs(min(0.0, max_down_pct))
        adverse_excursion = max(0.0, max_up_pct)

    return {
        "status": "OUTCOME_RESOLVED",
        "classification": classification,
        "evaluation_horizon_hours": float(policy.evaluation_horizon_hours),
        "horizon_end": horizon_end.isoformat(),
        "terminal_price_at": terminal_at.isoformat(),
        "terminal_gap_seconds": terminal_gap,
        "entry_btc_price": entry,
        "terminal_btc_price": float(terminal.btc_price),
        "terminal_return_pct": terminal_return,
        "neutral_band_pct": neutral,
        "realized_direction": realized_direction,
        "directional_hit": hit,
        "performance_eligible": performance_eligible,
        "max_up_pct": max_up_pct,
        "max_up_at": max_up_at.isoformat(),
        "max_down_pct": max_down_pct,
        "max_down_at": max_down_at.isoformat(),
        "max_abs_move_pct": max_abs_move_pct,
        "favorable_excursion_pct": favorable_excursion,
        "adverse_excursion_pct": adverse_excursion,
        "large_move_threshold_pct": float(policy.large_move_threshold_pct),
        "large_move_after_click": max_abs_move_pct >= float(policy.large_move_threshold_pct),
        "large_move_missed_during_abstention": (
            direction not in {"BULLISH", "BEARISH"}
            and max_abs_move_pct >= float(policy.large_move_threshold_pct)
        ),
        "outcome_used_for_decision": False,
        "decision_rewritten": False,
    }


def summarize_underlying_thesis_validation(rows: list[dict]) -> dict:
    input_resolved = [row for row in rows if row.get("status") == "CLICK_VALIDATED"]
    input_unresolved = [row for row in rows if row.get("status") == "CLICK_INPUT_UNRESOLVED"]
    outcomes = [row.get("outcome") or {} for row in input_resolved]
    outcome_unresolved = [row for row in outcomes if row.get("status") == "OUTCOME_UNRESOLVED"]
    hits = [row for row in outcomes if row.get("classification") == "DIRECTIONAL_HIT"]
    misses = [row for row in outcomes if row.get("classification") == "DIRECTIONAL_MISS"]
    inconclusive = [row for row in outcomes if row.get("classification") == "DIRECTIONAL_INCONCLUSIVE"]
    abstentions = [row for row in outcomes if row.get("classification") == "ABSTENTION_RESOLVED"]
    missed_large = [row for row in abstentions if row.get("large_move_missed_during_abstention") is True]
    scored = len(hits) + len(misses)
    directional_theses = sum(
        1
        for row in input_resolved
        if str((row.get("decision") or {}).get("market_direction", "UNKNOWN")).upper() in {"BULLISH", "BEARISH"}
    )
    return {
        "version": "BTC_UNDERLYING_THESIS_VALIDATION_SUMMARY_V1",
        "click_count": len(rows),
        "input_resolved_count": len(input_resolved),
        "input_unresolved_count": len(input_unresolved),
        "directional_thesis_count": directional_theses,
        "abstention_count": len(abstentions),
        "outcome_unresolved_count": len(outcome_unresolved),
        "directional_hit_count": len(hits),
        "directional_miss_count": len(misses),
        "directional_inconclusive_count": len(inconclusive),
        "directional_accuracy_denominator": scored,
        "directional_accuracy": None if scored == 0 else len(hits) / scored,
        "directional_coverage": None if not input_resolved else directional_theses / len(input_resolved),
        "abstention_large_move_count": len(missed_large),
        "flat_outcomes_excluded_from_accuracy": True,
        "abstentions_excluded_from_accuracy": True,
        "unresolved_outcomes_excluded_from_accuracy": True,
        "options_pnl_measured": False,
    }


def run_btc_underlying_thesis_validation(
    *,
    archive: BtcHistoricalArchive,
    policy: BtcUnderlyingThesisValidationPolicy,
) -> dict:
    """Run deterministic outcome-blind BTC thesis validation on a PIT archive."""
    archive.validated()
    policy.validated()
    clicks = generate_random_clicks(policy.click_policy)
    results: list[dict] = []

    for index, click in enumerate(clicks, start=1):
        click = _utc(click)
        click_id = f"btc-underlying-{policy.click_policy.seed}-{index:04d}-{int(click.timestamp())}"
        spot = latest_spot_candle(
            archive,
            as_of=click,
            max_age_seconds=int(policy.max_spot_age_seconds),
        )
        if spot is None:
            results.append({
                "version": "BTC_UNDERLYING_THESIS_CLICK_V1",
                "click_id": click_id,
                "decision_at": click.isoformat(),
                "status": "CLICK_INPUT_UNRESOLVED",
                "reason": "BTC_SPOT_AT_CLICK_MISSING_OR_STALE",
                "decision": None,
                "outcome": None,
                "performance_eligible": False,
                "options_contract_data_used": False,
                "options_execution_metadata_used": False,
                "futures_route_invoked": False,
                "futures_trade_generated": False,
                "live_execution": False,
            })
            continue

        evidence = visible_evidence_at(
            archive,
            decision_at=click,
            max_spot_age_seconds=int(policy.max_spot_age_seconds),
        )
        board = build_btc_information_board(
            evidence,
            decision_at=click,
            trade_horizon=policy.trade_horizon,
        )
        decision = _decision_snapshot(
            click_id=click_id,
            decision_at=click,
            btc_price=float(spot.close),
            board=board,
            evidence=evidence,
        )
        frozen_fingerprint = decision["decision_fingerprint"]

        forward = forward_btc_prices(
            archive,
            decision_at=click,
            horizon_hours=float(policy.evaluation_horizon_hours),
        )
        outcome = _evaluate_outcome(
            decision_at=click,
            entry_price=float(spot.close),
            market_direction=decision["market_direction"],
            forward_prices=forward,
            policy=policy,
        )
        if decision["decision_fingerprint"] != frozen_fingerprint:
            raise AssertionError("future outcome mutated the frozen decision fingerprint")

        results.append({
            "version": "BTC_UNDERLYING_THESIS_CLICK_V1",
            "click_id": click_id,
            "decision_at": click.isoformat(),
            "status": "CLICK_VALIDATED",
            "decision": decision,
            "outcome": outcome,
            "performance_eligible": outcome.get("performance_eligible") is True,
            "future_outcome_may_rewrite_decision": False,
            "options_contract_data_used": False,
            "options_execution_metadata_used": False,
            "options_pnl_measured": False,
            "futures_route_invoked": False,
            "futures_trade_generated": False,
            "live_execution": False,
        })

    summary = summarize_underlying_thesis_validation(results)
    return {
        "version": "BTC_UNDERLYING_THESIS_VALIDATION_V1",
        "asset": "BTC",
        "default_platform": "COINDCX",
        "scope": "UNDERLYING_DIRECTION_ONLY",
        "status": "VALIDATION_COMPLETE",
        "policy": {
            "trade_horizon": policy.trade_horizon,
            "evaluation_horizon_hours": float(policy.evaluation_horizon_hours),
            "terminal_price_max_gap_seconds": int(policy.terminal_price_max_gap_seconds),
            "neutral_band_pct": float(policy.neutral_band_pct),
            "large_move_threshold_pct": float(policy.large_move_threshold_pct),
            "click_seed": int(policy.click_policy.seed),
            "click_count": int(policy.click_policy.click_count),
        },
        "click_schedule": [click.isoformat() for click in clicks],
        "click_results": results,
        "summary": summary,
        "decision_inputs_point_in_time_only": True,
        "future_prices_attached_after_decision": True,
        "options_contract_data_required": False,
        "options_execution_metadata_required": False,
        "options_pnl_measured": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
        "live_execution": False,
        "capital_committed_live": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_UNDERLYING_THESIS_VALIDATION_CONTRACT_V1",
        "research_only": True,
        "random_clicks_outcome_blind": True,
        "decision_evidence_point_in_time_only": True,
        "decision_frozen_before_outcome": True,
        "terminal_return_is_primary_directional_outcome": True,
        "excursions_are_diagnostics_only": True,
        "flat_outcome_excluded_from_accuracy": True,
        "abstention_excluded_from_accuracy": True,
        "unresolved_outcome_excluded_from_accuracy": True,
        "options_contract_data_required": False,
        "options_execution_metadata_required": False,
        "options_pnl_measured": False,
        "historical_option_quotes_may_be_fabricated": False,
        "futures_route_invoked": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
        "live_execution": False,
    }
