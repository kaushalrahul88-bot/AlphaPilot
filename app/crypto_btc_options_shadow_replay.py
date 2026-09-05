"""Point-in-time BTC Options shadow trade lifecycle and replay evaluator.

This module is research/backtest only. It consumes an Options risk plan and exit
geometry, then walks observations chronologically. Underlying BTC invalidation,
underlying target, or time expiry creates the exit trigger; realized P&L is
priced from the first valid observed option bid after that trigger within an
explicit quote-delay tolerance. Modelled premium references are diagnostics only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Literal

ExitReason = Literal["UNDERLYING_INVALIDATION", "UNDERLYING_TARGET", "TIME_EXIT"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class BtcOptionsReplayObservation:
    observed_at: datetime
    btc_price: float
    option_bid: float | None
    option_ask: float | None = None
    source: str = "SHADOW_REPLAY"

    def validated(self) -> "BtcOptionsReplayObservation":
        if not isfinite(float(self.btc_price)) or self.btc_price <= 0:
            raise ValueError("btc_price must be finite and > 0")
        for name in ("option_bid", "option_ask"):
            value = getattr(self, name)
            if value is not None and (not isfinite(float(value)) or float(value) < 0):
                raise ValueError(f"{name} must be finite and >= 0 when supplied")
        if self.option_bid is not None and self.option_ask is not None and self.option_ask < self.option_bid:
            raise ValueError("option_ask cannot be below option_bid")
        return self


@dataclass(frozen=True)
class BtcOptionsReplayCostSpec:
    """Costs not already represented in the risk plan, supplied explicitly."""

    time_exit_fee_per_quantity_account: float
    fixed_time_exit_cost_account: float
    max_exit_quote_delay_seconds: int

    def validated(self) -> "BtcOptionsReplayCostSpec":
        for name in ("time_exit_fee_per_quantity_account", "fixed_time_exit_cost_account"):
            value = float(getattr(self, name))
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        if int(self.max_exit_quote_delay_seconds) < 0:
            raise ValueError("max_exit_quote_delay_seconds must be >= 0")
        return self


def _validate_inputs(risk: dict, geometry: dict) -> tuple[dict, str]:
    for payload, name in ((risk, "risk plan"), (geometry, "exit geometry")):
        if str(payload.get("instrument_type", "")).upper() != "OPTIONS":
            raise ValueError(f"{name} must be OPTIONS-only")
        if payload.get("futures_route_invoked") is True or payload.get("futures_trade_generated") is True:
            raise ValueError(f"{name} contains Futures-route state")
    if risk.get("status") != "OPTIONS_RISK_PLAN_READY":
        raise ValueError("shadow replay requires OPTIONS_RISK_PLAN_READY")
    if geometry.get("status") != "OPTIONS_EXIT_GEOMETRY_READY":
        raise ValueError("shadow replay requires OPTIONS_EXIT_GEOMETRY_READY")
    plan = risk.get("risk_plan")
    if not isinstance(plan, dict):
        raise ValueError("risk_plan payload is required")
    side = str(risk.get("side_candidate", "")).upper()
    if side not in {"BUY_CALL", "BUY_PUT"}:
        raise ValueError("shadow replay supports long Call/Put only")
    if str(geometry.get("side_candidate", "")).upper() != side:
        raise ValueError("risk plan and exit geometry side mismatch")
    risk_symbol = str(risk.get("selected_contract_symbol") or plan.get("contract_symbol") or "")
    geometry_symbol = str(geometry.get("symbol") or "")
    if not risk_symbol or risk_symbol != geometry_symbol:
        raise ValueError("risk plan and exit geometry contract symbol mismatch")
    if geometry.get("premium_projection_is_forecast") is not False:
        raise ValueError("shadow replay requires non-forecast premium geometry")
    if geometry.get("actual_quote_required_at_exit") is not True:
        raise ValueError("shadow replay requires actual option quote at exit")
    return plan, side


def _trigger_reason(*, side: str, btc_price: float, invalidation: float, target: float, observed_at: datetime, time_exit_at: datetime) -> ExitReason | None:
    if side == "BUY_CALL":
        if btc_price <= invalidation:
            return "UNDERLYING_INVALIDATION"
        if btc_price >= target:
            return "UNDERLYING_TARGET"
    else:
        if btc_price >= invalidation:
            return "UNDERLYING_INVALIDATION"
        if btc_price <= target:
            return "UNDERLYING_TARGET"
    if observed_at >= time_exit_at:
        return "TIME_EXIT"
    return None


def replay_btc_options_shadow_trade(
    *,
    decision_at: datetime,
    risk: dict,
    geometry: dict,
    observations: list[BtcOptionsReplayObservation],
    replay_costs: BtcOptionsReplayCostSpec,
) -> dict:
    """Resolve one shadow trade chronologically with no hindsight premium fill."""
    plan, side = _validate_inputs(risk, geometry)
    replay_costs = replay_costs.validated()
    decision = _utc(decision_at)

    if not observations:
        return _unresolved(side, "NO_REPLAY_OBSERVATIONS", "No future observations were supplied.")

    rows = []
    previous_at = None
    for item in observations:
        item.validated()
        at = _utc(item.observed_at)
        if at <= decision:
            raise ValueError("replay observations must be strictly after decision_at")
        if previous_at is not None and at < previous_at:
            raise ValueError("replay observations must be chronological")
        previous_at = at
        rows.append((at, item))

    invalidation = float(geometry["invalidation_btc_price"])
    target = float(geometry["target_btc_price"])
    time_exit_at = decision + timedelta(hours=float(geometry["time_exit_hours"]))

    pending_reason: ExitReason | None = None
    trigger_at: datetime | None = None
    trigger_btc_price: float | None = None
    trigger_option_bid: float | None = None
    exit_item: BtcOptionsReplayObservation | None = None
    exit_at: datetime | None = None

    for at, item in rows:
        if pending_reason is None:
            reason = _trigger_reason(
                side=side,
                btc_price=float(item.btc_price),
                invalidation=invalidation,
                target=target,
                observed_at=at,
                time_exit_at=time_exit_at,
            )
            if reason is not None:
                pending_reason = reason
                trigger_at = at
                trigger_btc_price = float(item.btc_price)
                trigger_option_bid = None if item.option_bid is None else float(item.option_bid)

        if pending_reason is not None and trigger_at is not None:
            delay = (at - trigger_at).total_seconds()
            if delay > replay_costs.max_exit_quote_delay_seconds:
                return _unresolved(
                    side,
                    "UNRESOLVED_EXIT_QUOTE_GAP",
                    "Exit trigger occurred but no valid option bid arrived inside the configured quote-delay tolerance.",
                    diagnostics={
                        "exit_reason": pending_reason,
                        "trigger_at": trigger_at.isoformat(),
                        "trigger_btc_price": trigger_btc_price,
                        "max_exit_quote_delay_seconds": replay_costs.max_exit_quote_delay_seconds,
                    },
                )
            if item.option_bid is not None and float(item.option_bid) > 0:
                exit_item = item
                exit_at = at
                break

    if pending_reason is None:
        return _unresolved(
            side,
            "NO_EXIT_TRIGGER_IN_WINDOW",
            "Replay window ended before BTC invalidation, BTC target, or time exit was observed.",
            diagnostics={"time_exit_at": time_exit_at.isoformat()},
        )
    if exit_item is None or exit_at is None or trigger_at is None:
        return _unresolved(
            side,
            "UNRESOLVED_EXIT_QUOTE_GAP",
            "Exit trigger occurred but no valid option bid was available in the supplied replay window.",
            diagnostics={
                "exit_reason": pending_reason,
                "trigger_at": trigger_at.isoformat(),
                "trigger_btc_price": trigger_btc_price,
            },
        )

    quantity = float(plan["quantity"])
    multiplier = float(plan["contract_multiplier"])
    fx = float(plan["premium_to_account_rate"])
    conversion = multiplier * fx
    entry_effective = float(plan["effective_entry_premium_after_slippage"])
    exit_slippage_pct = float(plan["cost_model"]["exit_slippage_pct_of_premium"])
    actual_exit_bid = float(exit_item.option_bid)
    effective_exit = max(0.0, actual_exit_bid * (1.0 - exit_slippage_pct / 100.0))

    cost_model = dict(plan["cost_model"])
    entry_fee_per_qty = float(cost_model["entry_fee_per_quantity_account"])
    fixed_entry = float(cost_model["fixed_entry_cost_account"])
    if pending_reason == "UNDERLYING_TARGET":
        exit_fee_per_qty = float(cost_model["target_exit_fee_per_quantity_account"])
        fixed_exit = float(cost_model["fixed_target_exit_cost_account"])
    elif pending_reason == "UNDERLYING_INVALIDATION":
        exit_fee_per_qty = float(cost_model["stop_exit_fee_per_quantity_account"])
        fixed_exit = float(cost_model["fixed_stop_exit_cost_account"])
    else:
        exit_fee_per_qty = float(replay_costs.time_exit_fee_per_quantity_account)
        fixed_exit = float(replay_costs.fixed_time_exit_cost_account)

    gross_pnl = (effective_exit - entry_effective) * conversion * quantity
    total_fees = (entry_fee_per_qty + exit_fee_per_qty) * quantity + fixed_entry + fixed_exit
    net_pnl = gross_pnl - total_fees
    premium_outlay = float(plan["premium_outlay"])
    planned_stop_loss = float(plan["planned_stop_loss"])
    net_return_pct = (net_pnl / premium_outlay * 100.0) if premium_outlay > 0 else None
    realized_r = (net_pnl / planned_stop_loss) if planned_stop_loss > 0 else None

    premium_reference = (
        float(geometry["target_premium_reference"])
        if pending_reason == "UNDERLYING_TARGET"
        else float(geometry["stop_premium_reference"])
        if pending_reason == "UNDERLYING_INVALIDATION"
        else None
    )
    quote_delay_seconds = (exit_at - trigger_at).total_seconds()

    return {
        "version": "BTC_OPTIONS_SHADOW_REPLAY_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "SHADOW_TRADE_CLOSED",
        "side_candidate": side,
        "contract_symbol": plan["contract_symbol"],
        "decision_at": decision.isoformat(),
        "trigger_at": trigger_at.isoformat(),
        "exit_at": exit_at.isoformat(),
        "exit_reason": pending_reason,
        "trigger_btc_price": trigger_btc_price,
        "exit_btc_price": float(exit_item.btc_price),
        "entry_effective_premium": entry_effective,
        "trigger_option_bid": trigger_option_bid,
        "actual_exit_bid": actual_exit_bid,
        "effective_exit_premium_after_slippage": effective_exit,
        "model_premium_reference_at_trigger": premium_reference,
        "model_reference_used_as_fill": False,
        "actual_quote_used_for_pnl": True,
        "exit_quote_delay_seconds": quote_delay_seconds,
        "quantity": quantity,
        "gross_pnl_account": gross_pnl,
        "total_fees_account": total_fees,
        "net_pnl_account": net_pnl,
        "net_return_pct_on_premium_outlay": net_return_pct,
        "realized_r_vs_planned_stop": realized_r,
        "account_currency": plan["account_currency"],
        "premium_outlay": premium_outlay,
        "planned_stop_loss": planned_stop_loss,
        "full_premium_tail_loss": float(plan["full_premium_tail_loss"]),
        "trade_generated": False,
        "order_created": False,
        "live_execution": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "capital_committed_live": 0,
    }


def _unresolved(side: str, status: str, reason: str, diagnostics: dict | None = None) -> dict:
    return {
        "version": "BTC_OPTIONS_SHADOW_REPLAY_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": status,
        "side_candidate": side,
        "reason": reason,
        "diagnostics": diagnostics or {},
        "shadow_trade_closed": False,
        "trade_generated": False,
        "order_created": False,
        "live_execution": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "capital_committed_live": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_SHADOW_REPLAY_CONTRACT_V1",
        "instrument_type": "OPTIONS",
        "chronological_replay_required": True,
        "future_observation_used_before_seen": False,
        "primary_stop_trigger": "UNDERLYING_INVALIDATION",
        "primary_target_trigger": "UNDERLYING_TARGET",
        "time_exit_supported": True,
        "actual_option_bid_required_for_realized_pnl": True,
        "model_premium_reference_used_as_fill": False,
        "quote_gap_imputation_allowed": False,
        "exit_quote_delay_tolerance_is_explicit_input": True,
        "futures_route_invoked": False,
        "futures_fallback_allowed": False,
        "live_execution": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
