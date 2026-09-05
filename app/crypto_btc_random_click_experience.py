"""BTC random-click backtest scheduling and post-decision Experience Ledger.

Research/shadow only. Random clicks are generated solely from the requested time
window and frozen seed. Future market outcomes never influence click selection.
The Experience Ledger attaches later trade outcomes or NO-TRADE missed-move
analytics strictly after the decision timestamp for post-decision learning.

Nothing in this module creates a live trade, changes the decision retrospectively,
or invokes the Futures route.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from random import Random
from statistics import mean
from typing import Literal

Decision = Literal["BUY_CALL", "BUY_PUT", "NO_TRADE"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite_positive(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return value


@dataclass(frozen=True)
class BtcRandomClickPolicy:
    start_at: datetime
    end_at: datetime
    click_count: int
    seed: int
    min_spacing_seconds: int = 0

    def validated(self) -> "BtcRandomClickPolicy":
        start = _utc(self.start_at)
        end = _utc(self.end_at)
        if end <= start:
            raise ValueError("end_at must be after start_at")
        if int(self.click_count) <= 0:
            raise ValueError("click_count must be > 0")
        if int(self.min_spacing_seconds) < 0:
            raise ValueError("min_spacing_seconds must be >= 0")
        span_seconds = int((end - start).total_seconds())
        if span_seconds <= 0:
            raise ValueError("click window must contain at least one whole second")
        effective_gap = max(1, int(self.min_spacing_seconds))
        required_span = int(self.click_count) + (int(self.click_count) - 1) * (effective_gap - 1)
        if required_span > span_seconds:
            raise ValueError("click_count/min_spacing_seconds cannot fit inside the requested window")
        return self


@dataclass(frozen=True)
class BtcForwardPriceObservation:
    observed_at: datetime
    btc_price: float

    def validated(self) -> "BtcForwardPriceObservation":
        _finite_positive("btc_price", self.btc_price)
        return self


@dataclass(frozen=True)
class BtcClickDecisionRecord:
    click_id: str
    decision_at: datetime
    decision_btc_price: float
    final_decision: Decision
    market_direction: str
    pipeline_status: str
    reason_codes: tuple[str, ...] = ()
    available_lanes: tuple[str, ...] = ()
    missing_lanes: tuple[str, ...] = ()
    latest_evidence_at: datetime | None = None
    instrument_type: str = "OPTIONS"
    futures_route_invoked: bool = False
    futures_trade_generated: bool = False

    def validated(self) -> "BtcClickDecisionRecord":
        if not str(self.click_id or "").strip():
            raise ValueError("click_id is required")
        decision_at = _utc(self.decision_at)
        _finite_positive("decision_btc_price", self.decision_btc_price)
        decision = str(self.final_decision).upper()
        if decision not in {"BUY_CALL", "BUY_PUT", "NO_TRADE"}:
            raise ValueError("final_decision must be BUY_CALL, BUY_PUT, or NO_TRADE")
        if str(self.instrument_type).upper() != "OPTIONS":
            raise ValueError("BTC random-click ledger is Options-only")
        if self.futures_route_invoked or self.futures_trade_generated:
            raise ValueError("BTC Options random-click ledger rejects Futures-route state")
        if self.latest_evidence_at is not None and _utc(self.latest_evidence_at) > decision_at:
            raise ValueError("decision contains evidence first seen after the click time")
        return self


@dataclass(frozen=True)
class BtcExperiencePolicy:
    no_trade_learning_horizon_hours: float
    large_move_threshold_pct: float

    def validated(self) -> "BtcExperiencePolicy":
        _finite_positive("no_trade_learning_horizon_hours", self.no_trade_learning_horizon_hours)
        _finite_positive("large_move_threshold_pct", self.large_move_threshold_pct)
        return self


def generate_random_clicks(policy: BtcRandomClickPolicy) -> list[datetime]:
    """Generate deterministic, outcome-blind, unique click timestamps.

    Times are sampled at one-second resolution from [start_at, end_at). The seed
    is frozen by the caller so a backtest can be reproduced exactly.
    """
    policy.validated()
    start = _utc(policy.start_at)
    end = _utc(policy.end_at)
    span_seconds = int((end - start).total_seconds())
    count = int(policy.click_count)
    gap = max(1, int(policy.min_spacing_seconds))

    # Transform sampled unique offsets so adjacent clicks are separated by at
    # least `gap`, while preserving deterministic random selection and avoiding
    # rejection loops that could behave poorly in dense windows.
    compressed_span = span_seconds - (count - 1) * (gap - 1)
    rng = Random(int(policy.seed))
    raw_offsets = sorted(rng.sample(range(compressed_span), count))
    offsets = [raw + index * (gap - 1) for index, raw in enumerate(raw_offsets)]
    clicks = [start + timedelta(seconds=offset) for offset in offsets]

    if any(click < start or click >= end for click in clicks):
        raise AssertionError("generated click escaped the requested window")
    if len(set(clicks)) != count:
        raise AssertionError("generated clicks are not unique")
    if any((b - a).total_seconds() < gap for a, b in zip(clicks, clicks[1:])):
        raise AssertionError("generated clicks violate minimum spacing")
    return clicks


def _validate_forward_prices(
    *,
    decision_at: datetime,
    rows: list[BtcForwardPriceObservation],
) -> list[tuple[datetime, BtcForwardPriceObservation]]:
    decision = _utc(decision_at)
    normalized: list[tuple[datetime, BtcForwardPriceObservation]] = []
    previous = None
    for row in rows:
        row.validated()
        at = _utc(row.observed_at)
        if at <= decision:
            raise ValueError("forward price observations must be strictly after decision_at")
        if previous is not None and at < previous:
            raise ValueError("forward price observations must be chronological")
        previous = at
        normalized.append((at, row))
    return normalized


def analyze_no_trade_follow_through(
    *,
    decision: BtcClickDecisionRecord,
    forward_prices: list[BtcForwardPriceObservation],
    experience_policy: BtcExperiencePolicy,
) -> dict:
    """Measure what happened after NO TRADE without rewriting the old decision."""
    decision.validated()
    experience_policy.validated()
    if str(decision.final_decision).upper() != "NO_TRADE":
        raise ValueError("NO-TRADE follow-through analysis requires final_decision=NO_TRADE")

    rows = _validate_forward_prices(decision_at=decision.decision_at, rows=forward_prices)
    horizon_end = _utc(decision.decision_at) + timedelta(hours=float(experience_policy.no_trade_learning_horizon_hours))
    rows = [(at, row) for at, row in rows if at <= horizon_end]
    if not rows:
        return {
            "status": "NO_TRADE_FOLLOW_THROUGH_UNRESOLVED",
            "reason": "No future BTC observations were available inside the learning horizon.",
            "large_move_missed": None,
            "decision_rewritten": False,
            "outcome_used_for_original_decision": False,
        }

    entry = float(decision.decision_btc_price)
    excursions = [((float(row.btc_price) - entry) / entry * 100.0, at) for at, row in rows]
    max_up_pct, max_up_at = max(excursions, key=lambda x: x[0])
    max_down_pct, max_down_at = min(excursions, key=lambda x: x[0])
    up_large = max_up_pct >= float(experience_policy.large_move_threshold_pct)
    down_large = abs(max_down_pct) >= float(experience_policy.large_move_threshold_pct)

    if up_large and down_large:
        move_class = "MISSED_LARGE_MOVE_BOTH_DIRECTIONS"
        missed_direction = "BOTH"
    elif up_large:
        move_class = "MISSED_LARGE_MOVE_UP"
        missed_direction = "UP"
    elif down_large:
        move_class = "MISSED_LARGE_MOVE_DOWN"
        missed_direction = "DOWN"
    else:
        move_class = "NO_LARGE_MOVE_AFTER_NO_TRADE"
        missed_direction = None

    max_abs_move_pct = max(abs(max_up_pct), abs(max_down_pct))
    return {
        "status": "NO_TRADE_FOLLOW_THROUGH_RESOLVED",
        "classification": move_class,
        "large_move_missed": bool(up_large or down_large),
        "missed_direction": missed_direction,
        "decision_btc_price": entry,
        "learning_horizon_hours": float(experience_policy.no_trade_learning_horizon_hours),
        "large_move_threshold_pct": float(experience_policy.large_move_threshold_pct),
        "max_up_pct": max_up_pct,
        "max_up_at": max_up_at.isoformat(),
        "max_down_pct": max_down_pct,
        "max_down_at": max_down_at.isoformat(),
        "max_abs_move_pct": max_abs_move_pct,
        "reason_codes_at_decision": list(decision.reason_codes),
        "missing_lanes_at_decision": list(decision.missing_lanes),
        "decision_rewritten": False,
        "outcome_used_for_original_decision": False,
        "requires_postmortem_if_large_move_missed": bool(up_large or down_large),
    }


def build_experience_entry(
    *,
    decision: BtcClickDecisionRecord,
    replay_result: dict | None,
    forward_prices: list[BtcForwardPriceObservation] | None,
    experience_policy: BtcExperiencePolicy,
) -> dict:
    """Create one immutable learning row from a click and its later outcome."""
    decision.validated()
    experience_policy.validated()
    final_decision = str(decision.final_decision).upper()
    decision_at = _utc(decision.decision_at)

    base = {
        "version": "BTC_RANDOM_CLICK_EXPERIENCE_V1",
        "click_id": decision.click_id,
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "decision_at": decision_at.isoformat(),
        "decision_btc_price": float(decision.decision_btc_price),
        "final_decision": final_decision,
        "market_direction": str(decision.market_direction).upper(),
        "pipeline_status": decision.pipeline_status,
        "reason_codes": list(decision.reason_codes),
        "available_lanes": list(decision.available_lanes),
        "missing_lanes": list(decision.missing_lanes),
        "latest_evidence_at": None if decision.latest_evidence_at is None else _utc(decision.latest_evidence_at).isoformat(),
        "decision_frozen_before_outcome": True,
        "future_outcome_may_rewrite_decision": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_live": 0,
    }

    if final_decision == "NO_TRADE":
        if replay_result is not None:
            raise ValueError("NO_TRADE experience entry cannot contain a trade replay result")
        follow = analyze_no_trade_follow_through(
            decision=decision,
            forward_prices=list(forward_prices or []),
            experience_policy=experience_policy,
        )
        return {
            **base,
            "outcome_type": "NO_TRADE_LEARNING",
            "trade_outcome": None,
            "no_trade_follow_through": follow,
            "performance_eligible": False,
            "postmortem_required": follow.get("requires_postmortem_if_large_move_missed") is True,
        }

    if replay_result is None:
        return {
            **base,
            "outcome_type": "TRADE_UNRESOLVED",
            "trade_outcome": None,
            "no_trade_follow_through": None,
            "performance_eligible": False,
            "postmortem_required": True,
            "unresolved_reason": "No shadow replay result supplied for trade decision.",
        }

    if str(replay_result.get("instrument_type", "")).upper() != "OPTIONS":
        raise ValueError("experience ledger accepts Options replay only")
    if replay_result.get("futures_route_invoked") is True or replay_result.get("futures_trade_generated") is True:
        raise ValueError("experience ledger rejects Futures-route replay state")
    replay_decision_at = replay_result.get("decision_at")
    if replay_decision_at is not None:
        parsed = datetime.fromisoformat(str(replay_decision_at).replace("Z", "+00:00"))
        if _utc(parsed) != decision_at:
            raise ValueError("replay decision_at does not match click decision_at")
    if str(replay_result.get("side_candidate", "")).upper() != final_decision:
        raise ValueError("replay side does not match frozen click decision")

    if replay_result.get("status") == "SHADOW_TRADE_CLOSED":
        net_pnl = float(replay_result["net_pnl_account"])
        realized_r = float(replay_result["realized_r_vs_planned_stop"])
        trade_outcome = {
            "status": "SHADOW_TRADE_CLOSED",
            "contract_symbol": replay_result.get("contract_symbol"),
            "exit_reason": replay_result.get("exit_reason"),
            "exit_at": replay_result.get("exit_at"),
            "net_pnl_account": net_pnl,
            "net_return_pct_on_premium_outlay": replay_result.get("net_return_pct_on_premium_outlay"),
            "realized_r_vs_planned_stop": realized_r,
            "actual_quote_used_for_pnl": replay_result.get("actual_quote_used_for_pnl") is True,
            "model_reference_used_as_fill": replay_result.get("model_reference_used_as_fill") is True,
            "win": net_pnl > 0,
        }
        return {
            **base,
            "outcome_type": "TRADE_CLOSED",
            "trade_outcome": trade_outcome,
            "no_trade_follow_through": None,
            "performance_eligible": True,
            "postmortem_required": net_pnl <= 0,
        }

    return {
        **base,
        "outcome_type": "TRADE_UNRESOLVED",
        "trade_outcome": {
            "status": replay_result.get("status"),
            "reason": replay_result.get("reason"),
            "diagnostics": replay_result.get("diagnostics", {}),
        },
        "no_trade_follow_through": None,
        "performance_eligible": False,
        "postmortem_required": True,
    }


def summarize_experience_ledger(entries: list[dict]) -> dict:
    """Summarize resolved performance while keeping unresolved rows excluded."""
    if not entries:
        return {
            "version": "BTC_RANDOM_CLICK_SUMMARY_V1",
            "click_count": 0,
            "performance_click_count": 0,
            "unresolved_excluded_from_performance": True,
        }

    click_ids = [str(row.get("click_id")) for row in entries]
    if len(set(click_ids)) != len(click_ids):
        raise ValueError("experience ledger contains duplicate click_id values")

    closed = [row for row in entries if row.get("outcome_type") == "TRADE_CLOSED" and row.get("performance_eligible") is True]
    unresolved = [row for row in entries if row.get("outcome_type") == "TRADE_UNRESOLVED"]
    no_trade = [row for row in entries if row.get("outcome_type") == "NO_TRADE_LEARNING"]
    no_trade_resolved = [
        row for row in no_trade
        if isinstance(row.get("no_trade_follow_through"), dict)
        and row["no_trade_follow_through"].get("status") == "NO_TRADE_FOLLOW_THROUGH_RESOLVED"
    ]
    missed = [row for row in no_trade_resolved if row["no_trade_follow_through"].get("large_move_missed") is True]

    pnls = [float(row["trade_outcome"]["net_pnl_account"]) for row in closed]
    rs = [float(row["trade_outcome"]["realized_r_vs_planned_stop"]) for row in closed]
    wins = [row for row in closed if row["trade_outcome"].get("win") is True]

    return {
        "version": "BTC_RANDOM_CLICK_SUMMARY_V1",
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "click_count": len(entries),
        "buy_call_count": sum(row.get("final_decision") == "BUY_CALL" for row in entries),
        "buy_put_count": sum(row.get("final_decision") == "BUY_PUT" for row in entries),
        "no_trade_count": len(no_trade),
        "closed_trade_count": len(closed),
        "unresolved_trade_count": len(unresolved),
        "performance_click_count": len(closed),
        "net_pnl_account": sum(pnls) if pnls else 0.0,
        "average_realized_r": mean(rs) if rs else None,
        "win_rate_pct": (len(wins) / len(closed) * 100.0) if closed else None,
        "resolved_no_trade_count": len(no_trade_resolved),
        "no_trade_large_move_missed_count": len(missed),
        "no_trade_large_move_missed_rate_pct": (
            len(missed) / len(no_trade_resolved) * 100.0 if no_trade_resolved else None
        ),
        "postmortem_required_count": sum(row.get("postmortem_required") is True for row in entries),
        "unresolved_excluded_from_performance": True,
        "no_trade_rows_excluded_from_trade_performance": True,
        "future_outcomes_used_to_select_clicks": False,
        "future_outcomes_used_to_rewrite_decisions": False,
        "futures_route_invoked": False,
        "live_execution": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_RANDOM_CLICK_EXPERIENCE_CONTRACT_V1",
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "crypto_market_is_24_7": True,
        "click_window_is_caller_defined_not_commodity_hours": True,
        "random_seed_is_explicit": True,
        "click_selection_uses_future_outcomes": False,
        "clicks_are_unique": True,
        "minimum_spacing_supported": True,
        "decision_evidence_must_not_be_after_click": True,
        "decision_is_frozen_before_outcome": True,
        "no_trade_follow_through_is_post_decision_learning_only": True,
        "no_trade_large_move_requires_postmortem": True,
        "unresolved_outcomes_excluded_from_performance": True,
        "actual_option_quote_required_by_shadow_replay_for_trade_pnl": True,
        "future_outcomes_may_rewrite_historical_decision": False,
        "futures_route_invoked": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
