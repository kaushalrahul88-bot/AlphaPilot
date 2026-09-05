"""Historical BTC Options random-click replay runner, research/shadow only.

This module composes the point-in-time historical adapter with the frozen BTC
click orchestrator. Click timestamps are generated without outcomes; each click
is decided first, fingerprinted, and only then receives later replay/NO-TRADE
follow-through data. Futures data may exist inside shared evidence, but this
runner can emit only BUY_CALL, BUY_PUT, NO_TRADE, or an unresolved-data row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite

from app.crypto_btc_click_orchestrator import (
    attach_btc_options_click_outcome,
    run_btc_options_click_decision,
)
from app.crypto_btc_historical_data_adapter import (
    BtcHistoricalArchive,
    forward_btc_prices,
    latest_execution_metadata_at,
    latest_spot_candle,
    option_replay_observations,
    source_coverage_at,
    structural_spot_window,
    visible_evidence_at,
    visible_option_contracts_at,
)
from app.crypto_btc_information_board import build_btc_information_board
from app.crypto_btc_options_contract_selector import BtcOptionsSelectionPolicy
from app.crypto_btc_options_exit_geometry import BtcOptionsGreekConvention, BtcOptionsUnderlyingThesis
from app.crypto_btc_options_risk import BtcOptionsRiskPolicy
from app.crypto_btc_random_click_experience import (
    BtcExperiencePolicy,
    BtcRandomClickPolicy,
    generate_random_clicks,
    summarize_experience_ledger,
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _positive(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return value


@dataclass(frozen=True)
class BtcHistoricalBacktestPolicy:
    click_policy: BtcRandomClickPolicy
    experience_policy: BtcExperiencePolicy
    trade_horizon: str
    max_spot_age_seconds: int
    structural_lookback_hours: float
    min_invalidation_distance_pct: float
    max_invalidation_distance_pct: float
    reward_multiple: float
    expected_holding_hours: float
    iv_stress_points: float = 0.0

    def validated(self) -> "BtcHistoricalBacktestPolicy":
        self.click_policy.validated()
        self.experience_policy.validated()
        if self.trade_horizon not in {"scalp", "intraday", "swing", "position"}:
            raise ValueError("unsupported trade_horizon")
        if int(self.max_spot_age_seconds) < 0:
            raise ValueError("max_spot_age_seconds must be >= 0")
        for name in (
            "structural_lookback_hours",
            "min_invalidation_distance_pct",
            "max_invalidation_distance_pct",
            "reward_multiple",
            "expected_holding_hours",
        ):
            _positive(name, getattr(self, name))
        if self.min_invalidation_distance_pct >= self.max_invalidation_distance_pct:
            raise ValueError("min_invalidation_distance_pct must be below max_invalidation_distance_pct")
        if not isfinite(float(self.iv_stress_points)) or float(self.iv_stress_points) < 0:
            raise ValueError("iv_stress_points must be finite and >= 0")
        return self


def _structural_thesis(
    archive: BtcHistoricalArchive,
    *,
    decision_at: datetime,
    direction: str,
    entry_price: float,
    policy: BtcHistoricalBacktestPolicy,
) -> tuple[BtcOptionsUnderlyingThesis | None, str | None]:
    direction = str(direction).upper()
    if direction not in {"BULLISH", "BEARISH"}:
        return None, None
    window = structural_spot_window(
        archive,
        decision_at=decision_at,
        lookback_hours=policy.structural_lookback_hours,
    )
    if not window:
        return None, "STRUCTURAL_LOOKBACK_MISSING"

    entry = float(entry_price)
    if direction == "BULLISH":
        invalidation = min(float(row.low) for row in window)
        distance = entry - invalidation
        if distance <= 0:
            return None, "BULLISH_INVALIDATION_NOT_BELOW_ENTRY"
        target = entry + distance * float(policy.reward_multiple)
    else:
        invalidation = max(float(row.high) for row in window)
        distance = invalidation - entry
        if distance <= 0:
            return None, "BEARISH_INVALIDATION_NOT_ABOVE_ENTRY"
        target = entry - distance * float(policy.reward_multiple)
        if target <= 0:
            return None, "BEARISH_TARGET_NONPOSITIVE"

    distance_pct = distance / entry * 100.0
    if distance_pct < float(policy.min_invalidation_distance_pct):
        return None, "STRUCTURAL_INVALIDATION_TOO_TIGHT"
    if distance_pct > float(policy.max_invalidation_distance_pct):
        return None, "STRUCTURAL_INVALIDATION_TOO_WIDE"

    return BtcOptionsUnderlyingThesis(
        entry_btc_price=entry,
        invalidation_btc_price=invalidation,
        target_btc_price=target,
        expected_holding_hours=float(policy.expected_holding_hours),
        stop_time_hours=float(policy.expected_holding_hours),
        target_time_hours=float(policy.expected_holding_hours),
        stop_iv_change_points=0.0,
        target_iv_change_points=0.0,
        iv_stress_points=float(policy.iv_stress_points),
    ), None


def _unresolved_input(*, click_id: str, decision_at: datetime, reason: str, coverage: dict) -> dict:
    return {
        "version": "BTC_HISTORICAL_CLICK_INPUT_V1",
        "click_id": click_id,
        "decision_at": _utc(decision_at).isoformat(),
        "status": "CLICK_INPUT_UNRESOLVED",
        "reason": reason,
        "coverage": coverage,
        "performance_eligible": False,
        "decision_generated": False,
        "outcome_attached": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
    }


def run_btc_historical_random_click_backtest(
    *,
    archive: BtcHistoricalArchive,
    policy: BtcHistoricalBacktestPolicy,
    risk_policy: BtcOptionsRiskPolicy,
    selection_policy: BtcOptionsSelectionPolicy | None = None,
    greek_convention: BtcOptionsGreekConvention | None = None,
) -> dict:
    """Run a deterministic point-in-time BTC Options shadow backtest."""
    archive.validated()
    policy.validated()
    risk_policy.validated()
    clicks = generate_random_clicks(policy.click_policy)

    click_results: list[dict] = []
    experience_rows: list[dict] = []
    unresolved_inputs: list[dict] = []

    for index, click in enumerate(clicks, start=1):
        click_id = f"btc-{policy.click_policy.seed}-{index:04d}-{int(_utc(click).timestamp())}"
        coverage = source_coverage_at(
            archive,
            decision_at=click,
            max_spot_age_seconds=policy.max_spot_age_seconds,
        )
        spot = latest_spot_candle(
            archive,
            as_of=click,
            max_age_seconds=policy.max_spot_age_seconds,
        )
        if spot is None:
            row = _unresolved_input(click_id=click_id, decision_at=click, reason="BTC_SPOT_AT_CLICK_MISSING_OR_STALE", coverage=coverage)
            unresolved_inputs.append(row)
            click_results.append(row)
            continue

        evidence = visible_evidence_at(
            archive,
            decision_at=click,
            max_spot_age_seconds=policy.max_spot_age_seconds,
        )
        board = build_btc_information_board(evidence, decision_at=click, trade_horizon=policy.trade_horizon)
        direction = str(board["underlying_market_state"].get("direction", "UNKNOWN")).upper()
        thesis, thesis_error = _structural_thesis(
            archive,
            decision_at=click,
            direction=direction,
            entry_price=float(spot.close),
            policy=policy,
        )
        if direction in {"BULLISH", "BEARISH"} and thesis is None:
            row = _unresolved_input(click_id=click_id, decision_at=click, reason=str(thesis_error or "STRUCTURAL_THESIS_UNAVAILABLE"), coverage=coverage)
            unresolved_inputs.append(row)
            click_results.append(row)
            continue

        execution = latest_execution_metadata_at(archive, decision_at=click)
        if execution is None:
            row = _unresolved_input(click_id=click_id, decision_at=click, reason="OPTIONS_EXECUTION_METADATA_MISSING", coverage=coverage)
            unresolved_inputs.append(row)
            click_results.append(row)
            continue

        contracts = visible_option_contracts_at(archive, decision_at=click)
        expected_move_pct = 0.0 if thesis is None else abs(float(thesis.target_btc_price) - float(spot.close)) / float(spot.close) * 100.0
        decision = run_btc_options_click_decision(
            click_id=click_id,
            decision_at=click,
            trade_horizon=policy.trade_horizon,
            evidence=evidence,
            contracts=contracts,
            btc_spot_price=float(spot.close),
            expected_move_pct=expected_move_pct,
            expected_holding_hours=float(policy.expected_holding_hours),
            fee_bps_per_side=float(execution.selector_fee_bps_per_side),
            underlying_thesis=thesis,
            risk_policy=risk_policy,
            execution_spec=execution.execution_spec,
            selection_policy=selection_policy,
            greek_convention=greek_convention,
            iv_percentile=None,
        )

        final_decision = str(decision["decision_record"]["final_decision"]).upper()
        if final_decision == "NO_TRADE":
            attached = attach_btc_options_click_outcome(
                decision_result=decision,
                experience_policy=policy.experience_policy,
                no_trade_forward_prices=forward_btc_prices(
                    archive,
                    decision_at=click,
                    horizon_hours=policy.experience_policy.no_trade_learning_horizon_hours,
                ),
            )
        else:
            symbol = str(decision["contract_selection"]["selected_contract"]["symbol"])
            replay_rows = option_replay_observations(
                archive,
                symbol=symbol,
                decision_at=click,
                horizon_hours=policy.expected_holding_hours,
                extra_quote_delay_seconds=execution.replay_costs.max_exit_quote_delay_seconds,
            )
            attached = attach_btc_options_click_outcome(
                decision_result=decision,
                experience_policy=policy.experience_policy,
                replay_observations=replay_rows,
                replay_costs=execution.replay_costs,
            )

        experience = attached["experience_entry"]
        experience_rows.append(experience)
        click_results.append({
            "version": "BTC_HISTORICAL_CLICK_RESULT_V1",
            "click_id": click_id,
            "decision_at": _utc(click).isoformat(),
            "status": "CLICK_REPLAY_ATTACHED",
            "coverage": coverage,
            "decision_fingerprint": decision["decision_fingerprint"],
            "final_decision": final_decision,
            "pipeline_status": decision["decision_record"]["pipeline_status"],
            "experience_outcome_type": experience["outcome_type"],
            "decision": decision,
            "outcome": attached,
            "futures_route_invoked": False,
            "futures_trade_generated": False,
            "live_execution": False,
        })

    experience_summary = summarize_experience_ledger(experience_rows) if experience_rows else {
        "click_count": 0,
        "closed_trade_count": 0,
        "unresolved_trade_count": 0,
        "no_trade_count": 0,
    }
    return {
        "version": "BTC_HISTORICAL_RANDOM_CLICK_BACKTEST_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "BACKTEST_COMPLETE",
        "click_count": len(clicks),
        "decision_and_outcome_attached_count": len(experience_rows),
        "input_unresolved_count": len(unresolved_inputs),
        "click_schedule": [_utc(click).isoformat() for click in clicks],
        "click_results": click_results,
        "experience_ledger": experience_rows,
        "experience_summary": experience_summary,
        "input_unresolved": unresolved_inputs,
        "click_selection_uses_future_outcomes": False,
        "unresolved_inputs_excluded_from_performance": True,
        "future_data_may_rewrite_decision": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
        "live_execution": False,
        "capital_committed_live": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_HISTORICAL_REPLAY_RUNNER_CONTRACT_V1",
        "research_only": True,
        "crypto_market_is_24_7": True,
        "random_clicks_outcome_blind": True,
        "point_in_time_adapter_required": True,
        "decision_frozen_before_outcome": True,
        "input_unresolved_rows_excluded_from_performance": True,
        "structural_invalidation_uses_only_preclick_completed_candles": True,
        "premium_stop_is_primary_trigger": False,
        "actual_option_quote_required_for_trade_pnl": True,
        "historical_options_may_be_fabricated": False,
        "futures_route_invoked": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
    }
