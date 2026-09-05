"""Point-in-time BTC Options click orchestrator, research/shadow only.

Stitches the already-separated Crypto Brain components into one auditable click
pipeline without adding a broker/data-provider dependency. Decision inputs must
already be point-in-time snapshots. Future evidence/quotes fail closed rather
than being silently ignored, and future outcome observations enter only through
the separate outcome-attachment function after the decision is frozen.

OPTIONS and FUTURES remain hard-separated: perpetual/futures evidence may appear
inside the shared market evidence list, but this orchestrator can emit only
BUY_CALL, BUY_PUT, or NO_TRADE and never invokes a Futures trade route.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from app.crypto_btc_information_board import build_btc_information_board
from app.crypto_btc_options_contract_selector import (
    BtcOptionContractSnapshot,
    BtcOptionsSelectionPolicy,
    select_btc_option_contract,
)
from app.crypto_btc_options_exit_geometry import (
    BtcOptionsGreekConvention,
    BtcOptionsUnderlyingThesis,
    build_btc_options_exit_geometry,
)
from app.crypto_btc_options_preflight import options_route_preflight
from app.crypto_btc_options_risk import (
    BtcOptionsExecutionSpec,
    BtcOptionsRiskPolicy,
    BtcOptionsRiskScenario,
    build_btc_options_risk_plan,
)
from app.crypto_btc_options_shadow_replay import (
    BtcOptionsReplayCostSpec,
    BtcOptionsReplayObservation,
    replay_btc_options_shadow_trade,
)
from app.crypto_btc_random_click_experience import (
    BtcClickDecisionRecord,
    BtcExperiencePolicy,
    BtcForwardPriceObservation,
    build_experience_entry,
)
from app.crypto_market_intelligence import Evidence, evidence_is_fresh


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return _canonical(asdict(value))
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def _latest_visible_at(rows: list[Evidence], decision_at: datetime) -> datetime | None:
    visible = [_utc(row.observed_at) for row in rows if _utc(row.observed_at) <= _utc(decision_at)]
    return max(visible) if visible else None


def _options_context(rows: list[Evidence], *, decision_at: datetime, trade_horizon: str) -> Evidence | None:
    candidates = [
        row
        for row in rows
        if row.family == "BTC_OPTIONS_MARKET"
        and evidence_is_fresh(row, decision_at=decision_at, trade_horizon=trade_horizon)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: _utc(row.observed_at))[-1]


def _available_lanes(board: dict) -> tuple[str, ...]:
    return tuple(sorted(lane for lane, row in board.get("lane_status", {}).items() if row.get("available") is True))


def _decision_record(
    *,
    click_id: str,
    decision_at: datetime,
    btc_spot_price: float,
    final_decision: str,
    market_direction: str,
    pipeline_status: str,
    reason_codes: list[str],
    board: dict,
    evidence: list[Evidence],
) -> BtcClickDecisionRecord:
    return BtcClickDecisionRecord(
        click_id=click_id,
        decision_at=_utc(decision_at),
        decision_btc_price=float(btc_spot_price),
        final_decision=final_decision,
        market_direction=market_direction,
        pipeline_status=pipeline_status,
        reason_codes=tuple(reason_codes),
        available_lanes=_available_lanes(board),
        missing_lanes=tuple(board.get("missing_lanes", [])),
        latest_evidence_at=_latest_visible_at(evidence, decision_at),
        instrument_type="OPTIONS",
        futures_route_invoked=False,
        futures_trade_generated=False,
    ).validated()


def _freeze_result(
    *,
    decision_record: BtcClickDecisionRecord,
    board: dict,
    preflight: dict | None,
    contract_selection: dict | None,
    geometry: dict | None,
    risk: dict | None,
) -> dict:
    frozen_payload = {
        "decision_record": asdict(decision_record),
        "information_board": board,
        "options_preflight": preflight,
        "contract_selection": contract_selection,
        "exit_geometry": geometry,
        "risk": risk,
    }
    fingerprint = _digest(frozen_payload)
    return {
        "version": "BTC_OPTIONS_CLICK_ORCHESTRATOR_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "CLICK_DECISION_FROZEN",
        "decision_record": asdict(decision_record),
        "information_board": board,
        "options_preflight": preflight,
        "contract_selection": contract_selection,
        "exit_geometry": geometry,
        "risk": risk,
        "decision_fingerprint": fingerprint,
        "decision_frozen_before_outcome": True,
        "future_outcome_present_in_decision_payload": False,
        "trade_generated": False,
        "order_created": False,
        "live_execution": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "futures_fallback_allowed": False,
        "capital_committed_live": 0,
    }


def _assert_decision_inputs_are_point_in_time(
    *,
    decision_at: datetime,
    evidence: list[Evidence],
    contracts: list[BtcOptionContractSnapshot],
) -> None:
    decision = _utc(decision_at)
    future_evidence = [row for row in evidence if _utc(row.observed_at) > decision]
    if future_evidence:
        raise ValueError("point-in-time click bundle contains evidence observed after decision_at")
    future_contracts = [row for row in contracts if _utc(row.observed_at) > decision]
    if future_contracts:
        raise ValueError("point-in-time click bundle contains option quotes observed after decision_at")


def run_btc_options_click_decision(
    *,
    click_id: str,
    decision_at: datetime,
    trade_horizon: str,
    evidence: list[Evidence],
    contracts: list[BtcOptionContractSnapshot],
    btc_spot_price: float,
    expected_move_pct: float,
    expected_holding_hours: float,
    fee_bps_per_side: float,
    underlying_thesis: BtcOptionsUnderlyingThesis | None,
    risk_policy: BtcOptionsRiskPolicy | None,
    execution_spec: BtcOptionsExecutionSpec | None,
    selection_policy: BtcOptionsSelectionPolicy | None = None,
    greek_convention: BtcOptionsGreekConvention | None = None,
    iv_percentile: float | None = None,
) -> dict:
    """Freeze one click decision using only information available at the click."""
    if not str(click_id or "").strip():
        raise ValueError("click_id is required")
    if float(btc_spot_price) <= 0:
        raise ValueError("btc_spot_price must be > 0")
    _assert_decision_inputs_are_point_in_time(
        decision_at=decision_at,
        evidence=evidence,
        contracts=contracts,
    )

    board = build_btc_information_board(evidence, decision_at=decision_at, trade_horizon=trade_horizon)
    market_state = dict(board["underlying_market_state"])
    market_direction = str(market_state.get("direction", "UNKNOWN")).upper()
    options_context = _options_context(evidence, decision_at=decision_at, trade_horizon=trade_horizon)
    preflight = options_route_preflight(btc_market_state=market_state, options_context=options_context)

    if preflight.get("contract_selection_allowed") is not True:
        reason_codes = [str(preflight.get("status") or "OPTIONS_PREFLIGHT_BLOCKED")]
        if market_direction == "UNKNOWN":
            reason_codes.append(str(market_state.get("state") or "NO_UNDERLYING_THESIS"))
        record = _decision_record(
            click_id=click_id,
            decision_at=decision_at,
            btc_spot_price=btc_spot_price,
            final_decision="NO_TRADE",
            market_direction=market_direction,
            pipeline_status=str(preflight.get("status") or "OPTIONS_PREFLIGHT_BLOCKED"),
            reason_codes=reason_codes,
            board=board,
            evidence=evidence,
        )
        return _freeze_result(
            decision_record=record,
            board=board,
            preflight=preflight,
            contract_selection=None,
            geometry=None,
            risk=None,
        )

    selection = select_btc_option_contract(
        options_preflight=preflight,
        contracts=contracts,
        decision_at=decision_at,
        btc_spot_price=btc_spot_price,
        expected_move_pct=expected_move_pct,
        expected_holding_hours=expected_holding_hours,
        fee_bps_per_side=fee_bps_per_side,
        iv_percentile=iv_percentile,
        policy=selection_policy,
    )
    if selection.get("status") != "OPTIONS_CONTRACT_CANDIDATE_SELECTED":
        rejection_reasons = sorted(
            {
                reason
                for row in selection.get("evaluated_contracts", [])
                for reason in row.get("rejection_reasons", [])
            }
        )
        record = _decision_record(
            click_id=click_id,
            decision_at=decision_at,
            btc_spot_price=btc_spot_price,
            final_decision="NO_TRADE",
            market_direction=market_direction,
            pipeline_status="NO_OPTIONS_CONTRACT",
            reason_codes=["NO_OPTIONS_CONTRACT", *rejection_reasons],
            board=board,
            evidence=evidence,
        )
        return _freeze_result(
            decision_record=record,
            board=board,
            preflight=preflight,
            contract_selection=selection,
            geometry=None,
            risk=None,
        )

    if underlying_thesis is None:
        record = _decision_record(
            click_id=click_id,
            decision_at=decision_at,
            btc_spot_price=btc_spot_price,
            final_decision="NO_TRADE",
            market_direction=market_direction,
            pipeline_status="UNDERLYING_EXIT_THESIS_MISSING",
            reason_codes=["UNDERLYING_EXIT_THESIS_MISSING"],
            board=board,
            evidence=evidence,
        )
        return _freeze_result(
            decision_record=record,
            board=board,
            preflight=preflight,
            contract_selection=selection,
            geometry=None,
            risk=None,
        )

    entry_delta = abs(float(underlying_thesis.entry_btc_price) - float(btc_spot_price))
    entry_tolerance = max(1e-9, abs(float(btc_spot_price)) * 1e-9)
    if entry_delta > entry_tolerance:
        raise ValueError("underlying_thesis entry_btc_price must match click-time btc_spot_price")

    geometry = build_btc_options_exit_geometry(
        contract_selection=selection,
        thesis=underlying_thesis,
        greek_convention=greek_convention,
    )

    if risk_policy is None or execution_spec is None:
        record = _decision_record(
            click_id=click_id,
            decision_at=decision_at,
            btc_spot_price=btc_spot_price,
            final_decision="NO_TRADE",
            market_direction=market_direction,
            pipeline_status="OPTIONS_RISK_POLICY_MISSING",
            reason_codes=["OPTIONS_RISK_POLICY_MISSING"],
            board=board,
            evidence=evidence,
        )
        return _freeze_result(
            decision_record=record,
            board=board,
            preflight=preflight,
            contract_selection=selection,
            geometry=geometry,
            risk=None,
        )

    scenario = BtcOptionsRiskScenario(**dict(geometry["risk_scenario"]))
    risk = build_btc_options_risk_plan(
        contract_selection=selection,
        risk_policy=risk_policy,
        execution_spec=execution_spec,
        scenario=scenario,
    )
    if risk.get("status") != "OPTIONS_RISK_PLAN_READY":
        record = _decision_record(
            click_id=click_id,
            decision_at=decision_at,
            btc_spot_price=btc_spot_price,
            final_decision="NO_TRADE",
            market_direction=market_direction,
            pipeline_status=str(risk.get("status") or "NO_OPTIONS_RISK_PLAN"),
            reason_codes=["NO_OPTIONS_RISK_PLAN", str(risk.get("reason") or "RISK_GATE_BLOCKED")],
            board=board,
            evidence=evidence,
        )
        return _freeze_result(
            decision_record=record,
            board=board,
            preflight=preflight,
            contract_selection=selection,
            geometry=geometry,
            risk=risk,
        )

    side = str(preflight["side_candidate"]).upper()
    record = _decision_record(
        click_id=click_id,
        decision_at=decision_at,
        btc_spot_price=btc_spot_price,
        final_decision=side,
        market_direction=market_direction,
        pipeline_status="OPTIONS_SHADOW_PLAN_READY",
        reason_codes=[],
        board=board,
        evidence=evidence,
    )
    return _freeze_result(
        decision_record=record,
        board=board,
        preflight=preflight,
        contract_selection=selection,
        geometry=geometry,
        risk=risk,
    )


def verify_frozen_click_decision(decision_result: dict) -> bool:
    payload = {
        "decision_record": decision_result.get("decision_record"),
        "information_board": decision_result.get("information_board"),
        "options_preflight": decision_result.get("options_preflight"),
        "contract_selection": decision_result.get("contract_selection"),
        "exit_geometry": decision_result.get("exit_geometry"),
        "risk": decision_result.get("risk"),
    }
    return _digest(payload) == decision_result.get("decision_fingerprint")


def attach_btc_options_click_outcome(
    *,
    decision_result: dict,
    experience_policy: BtcExperiencePolicy,
    replay_observations: list[BtcOptionsReplayObservation] | None = None,
    replay_costs: BtcOptionsReplayCostSpec | None = None,
    no_trade_forward_prices: list[BtcForwardPriceObservation] | None = None,
) -> dict:
    """Attach later outcome data only after verifying the frozen decision."""
    if str(decision_result.get("instrument_type", "")).upper() != "OPTIONS":
        raise ValueError("BTC click outcome attachment accepts Options decision only")
    if decision_result.get("futures_route_invoked") is True or decision_result.get("futures_trade_generated") is True:
        raise ValueError("BTC click outcome attachment rejects Futures-route state")
    if not verify_frozen_click_decision(decision_result):
        raise ValueError("frozen click decision fingerprint mismatch")

    raw_record = dict(decision_result["decision_record"])
    raw_record["decision_at"] = datetime.fromisoformat(str(raw_record["decision_at"]).replace("Z", "+00:00"))
    if raw_record.get("latest_evidence_at") is not None:
        raw_record["latest_evidence_at"] = datetime.fromisoformat(
            str(raw_record["latest_evidence_at"]).replace("Z", "+00:00")
        )
    raw_record["reason_codes"] = tuple(raw_record.get("reason_codes", []))
    raw_record["available_lanes"] = tuple(raw_record.get("available_lanes", []))
    raw_record["missing_lanes"] = tuple(raw_record.get("missing_lanes", []))
    record = BtcClickDecisionRecord(**raw_record).validated()

    if str(record.final_decision).upper() == "NO_TRADE":
        if replay_observations:
            raise ValueError("NO_TRADE click cannot receive Options replay observations")
        experience = build_experience_entry(
            decision=record,
            replay_result=None,
            forward_prices=list(no_trade_forward_prices or []),
            experience_policy=experience_policy,
        )
        return {
            "version": "BTC_OPTIONS_CLICK_OUTCOME_V1",
            "decision_fingerprint": decision_result["decision_fingerprint"],
            "decision_unchanged": verify_frozen_click_decision(decision_result),
            "replay_result": None,
            "experience_entry": experience,
            "futures_route_invoked": False,
            "live_execution": False,
        }

    if replay_costs is None:
        raise ValueError("trade click outcome requires explicit replay_costs")
    replay = replay_btc_options_shadow_trade(
        decision_at=record.decision_at,
        risk=decision_result["risk"],
        geometry=decision_result["exit_geometry"],
        observations=list(replay_observations or []),
        replay_costs=replay_costs,
    )
    experience = build_experience_entry(
        decision=record,
        replay_result=replay,
        forward_prices=None,
        experience_policy=experience_policy,
    )
    return {
        "version": "BTC_OPTIONS_CLICK_OUTCOME_V1",
        "decision_fingerprint": decision_result["decision_fingerprint"],
        "decision_unchanged": verify_frozen_click_decision(decision_result),
        "replay_result": replay,
        "experience_entry": experience,
        "futures_route_invoked": False,
        "live_execution": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_CLICK_ORCHESTRATOR_CONTRACT_V1",
        "instrument_type": "OPTIONS",
        "point_in_time_bundle_required": True,
        "future_evidence_is_rejected_not_ignored": True,
        "future_option_quote_is_rejected_not_ignored": True,
        "decision_and_outcome_phases_are_separate": True,
        "decision_fingerprint_required_before_outcome_attachment": True,
        "underlying_thesis_entry_must_match_click_spot": True,
        "missing_options_context_returns_no_trade": True,
        "no_eligible_contract_returns_no_trade": True,
        "missing_risk_policy_returns_no_trade": True,
        "failed_risk_gate_returns_no_trade": True,
        "futures_context_may_inform_shared_market_state": True,
        "futures_trade_generation_allowed": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
        "live_execution": False,
        "research_only": True,
    }
