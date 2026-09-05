"""BTC Options risk and position-sizing brain, research/shadow only.

This module is strictly downstream of the BTC Options contract selector. It
validates a long-option stop/target scenario, applies an explicitly supplied
crypto risk policy, and sizes quantity conservatively against three independent
ceilings:

1. premium/capital allocation,
2. planned stop loss, and
3. full-premium tail loss.

A planned stop is *not* treated as guaranteed maximum loss. Long options can gap
or become difficult to exit, so the full premium paid remains a separate tail
risk constraint.

No crypto capital limit is hard-coded here. In particular, the Commodity Brain's
₹15,000 rule is not inherited. Fees, slippage, contract multiplier, quantity
step and currency conversion are explicit point-in-time inputs so a later
CoinDCX adapter can supply real platform metadata without this module inventing
contract conventions.

This module selects no Futures position and has no Futures fallback. It creates
no broker order and commits no capital.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor, isfinite


def _positive(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return value


def _nonnegative(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")
    return value


def _pct_0_100(name: str, value: float, *, allow_zero: bool = False) -> float:
    value = float(value)
    lower_ok = value >= 0 if allow_zero else value > 0
    if not isfinite(value) or not lower_ok or value > 100:
        boundary = "0..100" if allow_zero else ">0..100"
        raise ValueError(f"{name} must be finite and within {boundary}")
    return value


def _floor_to_step(value: float, step: float) -> float:
    if value <= 0:
        return 0.0
    units = floor((value + 1e-12) / step)
    return float(round(units * step, 12))


def _ceil_to_step(value: float, step: float) -> float:
    if value <= 0:
        return 0.0
    units = floor((value - 1e-12) / step) + 1
    return float(round(units * step, 12))


@dataclass(frozen=True)
class BtcOptionsRiskPolicy:
    """Explicit crypto-specific risk policy; there are intentionally no defaults."""

    account_equity: float
    max_premium_allocation_pct_of_equity: float
    max_planned_loss_pct_of_equity: float
    max_tail_loss_pct_of_equity: float
    min_net_reward_risk: float
    max_premium_allocation_absolute: float | None = None

    def validated(self) -> "BtcOptionsRiskPolicy":
        _positive("account_equity", self.account_equity)
        _pct_0_100("max_premium_allocation_pct_of_equity", self.max_premium_allocation_pct_of_equity)
        _pct_0_100("max_planned_loss_pct_of_equity", self.max_planned_loss_pct_of_equity)
        _pct_0_100("max_tail_loss_pct_of_equity", self.max_tail_loss_pct_of_equity)
        _positive("min_net_reward_risk", self.min_net_reward_risk)
        if self.max_premium_allocation_absolute is not None:
            _positive("max_premium_allocation_absolute", self.max_premium_allocation_absolute)
        return self


@dataclass(frozen=True)
class BtcOptionsExecutionSpec:
    """Point-in-time contract/execution metadata supplied by the platform adapter."""

    account_currency: str
    premium_currency: str
    premium_to_account_rate: float
    contract_multiplier: float
    quantity_step: float
    min_quantity: float
    max_quantity: float | None
    entry_slippage_pct_of_premium: float
    exit_slippage_pct_of_premium: float
    entry_fee_per_quantity_account: float
    stop_exit_fee_per_quantity_account: float
    target_exit_fee_per_quantity_account: float
    fixed_entry_cost_account: float = 0.0
    fixed_stop_exit_cost_account: float = 0.0
    fixed_target_exit_cost_account: float = 0.0

    def validated(self) -> "BtcOptionsExecutionSpec":
        if not str(self.account_currency or "").strip():
            raise ValueError("account_currency is required")
        if not str(self.premium_currency or "").strip():
            raise ValueError("premium_currency is required")
        _positive("premium_to_account_rate", self.premium_to_account_rate)
        _positive("contract_multiplier", self.contract_multiplier)
        _positive("quantity_step", self.quantity_step)
        _positive("min_quantity", self.min_quantity)
        if self.max_quantity is not None:
            _positive("max_quantity", self.max_quantity)
            if self.max_quantity < self.min_quantity:
                raise ValueError("max_quantity cannot be below min_quantity")
        _pct_0_100("entry_slippage_pct_of_premium", self.entry_slippage_pct_of_premium, allow_zero=True)
        _pct_0_100("exit_slippage_pct_of_premium", self.exit_slippage_pct_of_premium, allow_zero=True)
        for name in (
            "entry_fee_per_quantity_account",
            "stop_exit_fee_per_quantity_account",
            "target_exit_fee_per_quantity_account",
            "fixed_entry_cost_account",
            "fixed_stop_exit_cost_account",
            "fixed_target_exit_cost_account",
        ):
            _nonnegative(name, getattr(self, name))
        return self


@dataclass(frozen=True)
class BtcOptionsRiskScenario:
    """Explicit long-option premium geometry produced upstream and validated here."""

    stop_premium: float
    target_premium: float
    stop_basis: str
    target_basis: str

    def validated_against_entry(self, entry_premium: float) -> "BtcOptionsRiskScenario":
        _positive("entry_premium", entry_premium)
        _nonnegative("stop_premium", self.stop_premium)
        _positive("target_premium", self.target_premium)
        if self.stop_premium >= entry_premium:
            raise ValueError("long-option stop_premium must be below entry premium")
        if self.target_premium <= entry_premium:
            raise ValueError("long-option target_premium must be above entry premium")
        if not str(self.stop_basis or "").strip():
            raise ValueError("stop_basis is required")
        if not str(self.target_basis or "").strip():
            raise ValueError("target_basis is required")
        return self


def _no_plan(*, side: str, reason: str, status: str, diagnostics: dict | None = None) -> dict:
    return {
        "version": "BTC_OPTIONS_RISK_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": status,
        "side_candidate": side,
        "reason": reason,
        "risk_plan": None,
        "diagnostics": diagnostics or {},
        "quantity_selected": False,
        "trade_generated": False,
        "order_created": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }


def build_btc_options_risk_plan(
    *,
    contract_selection: dict,
    risk_policy: BtcOptionsRiskPolicy,
    execution_spec: BtcOptionsExecutionSpec,
    scenario: BtcOptionsRiskScenario,
) -> dict:
    """Build a conservative, non-executable long BTC option risk plan."""
    if str(contract_selection.get("instrument_type", "")).upper() != "OPTIONS":
        raise ValueError("BTC Options risk brain accepts OPTIONS contract selection only")
    if contract_selection.get("futures_route_invoked") is True or contract_selection.get("futures_trade_generated") is True:
        raise ValueError("BTC Options risk brain rejects any Futures-route state")
    if contract_selection.get("trade_generated") is True or contract_selection.get("order_created") is True:
        raise ValueError("BTC Options risk brain requires a pre-trade, pre-order selection")

    side = str(contract_selection.get("side_candidate", "NO_TRADE")).upper()
    if side not in {"BUY_CALL", "BUY_PUT", "NO_TRADE"}:
        raise ValueError(f"unsupported Options side candidate: {side}")
    if side == "NO_TRADE" or contract_selection.get("status") != "OPTIONS_CONTRACT_CANDIDATE_SELECTED":
        return _no_plan(
            side=side,
            status="NO_OPTIONS_RISK_PLAN",
            reason="Contract selector did not provide an eligible Options contract.",
        )

    selected = contract_selection.get("selected_contract")
    if not isinstance(selected, dict) or selected.get("eligible") is not True:
        raise ValueError("selected_contract must be an eligible contract evaluation")
    metrics = selected.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("selected_contract metrics are required")
    if str(metrics.get("platform", "")).upper() != "COINDCX":
        raise ValueError("BTC Options risk brain requires CoinDCX contract metadata by default")

    expected_option_type = "CALL" if side == "BUY_CALL" else "PUT"
    if str(metrics.get("option_type", "")).upper() != expected_option_type:
        raise ValueError("selected contract option type does not match Options side candidate")

    ask = _positive("selected contract ask", metrics.get("ask"))
    policy = risk_policy.validated()
    spec = execution_spec.validated()
    scenario.validated_against_entry(ask)

    entry_slip = spec.entry_slippage_pct_of_premium / 100.0
    exit_slip = spec.exit_slippage_pct_of_premium / 100.0
    effective_entry = ask * (1.0 + entry_slip)
    effective_stop_exit = max(0.0, scenario.stop_premium * (1.0 - exit_slip))
    effective_target_exit = scenario.target_premium * (1.0 - exit_slip)

    if effective_stop_exit >= effective_entry:
        raise ValueError("effective stop exit must remain below effective entry")
    if effective_target_exit <= effective_entry:
        raise ValueError("effective target exit must remain above effective entry")

    conversion = spec.contract_multiplier * spec.premium_to_account_rate
    premium_outlay_per_qty = effective_entry * conversion + spec.entry_fee_per_quantity_account
    planned_loss_per_qty = (
        (effective_entry - effective_stop_exit) * conversion
        + spec.entry_fee_per_quantity_account
        + spec.stop_exit_fee_per_quantity_account
    )
    tail_loss_per_qty = effective_entry * conversion + spec.entry_fee_per_quantity_account
    net_reward_per_qty = (
        (effective_target_exit - effective_entry) * conversion
        - spec.entry_fee_per_quantity_account
        - spec.target_exit_fee_per_quantity_account
    )

    if premium_outlay_per_qty <= 0 or planned_loss_per_qty <= 0 or tail_loss_per_qty <= 0:
        raise ValueError("computed per-quantity risk values must be positive")
    if net_reward_per_qty <= 0:
        return _no_plan(
            side=side,
            status="NO_OPTIONS_RISK_PLAN",
            reason="Target does not produce positive net reward after supplied costs/slippage.",
            diagnostics={"net_reward_per_quantity": net_reward_per_qty},
        )

    equity = float(policy.account_equity)
    premium_budget_pct = equity * policy.max_premium_allocation_pct_of_equity / 100.0
    premium_budget = premium_budget_pct
    if policy.max_premium_allocation_absolute is not None:
        premium_budget = min(premium_budget, float(policy.max_premium_allocation_absolute))
    planned_risk_budget = equity * policy.max_planned_loss_pct_of_equity / 100.0
    tail_risk_budget = equity * policy.max_tail_loss_pct_of_equity / 100.0

    available_premium_budget = premium_budget - spec.fixed_entry_cost_account
    available_planned_budget = planned_risk_budget - spec.fixed_entry_cost_account - spec.fixed_stop_exit_cost_account
    available_tail_budget = tail_risk_budget - spec.fixed_entry_cost_account
    if min(available_premium_budget, available_planned_budget, available_tail_budget) <= 0:
        return _no_plan(
            side=side,
            status="NO_OPTIONS_RISK_PLAN",
            reason="Fixed costs consume one or more configured risk budgets.",
            diagnostics={
                "premium_budget": premium_budget,
                "planned_risk_budget": planned_risk_budget,
                "tail_risk_budget": tail_risk_budget,
            },
        )

    qty_by_premium = _floor_to_step(available_premium_budget / premium_outlay_per_qty, spec.quantity_step)
    qty_by_planned = _floor_to_step(available_planned_budget / planned_loss_per_qty, spec.quantity_step)
    qty_by_tail = _floor_to_step(available_tail_budget / tail_loss_per_qty, spec.quantity_step)
    quantity_caps = {
        "premium_allocation": qty_by_premium,
        "planned_stop_risk": qty_by_planned,
        "full_premium_tail_risk": qty_by_tail,
    }
    if spec.max_quantity is not None:
        quantity_caps["platform_max_quantity"] = _floor_to_step(float(spec.max_quantity), spec.quantity_step)

    quantity = _floor_to_step(min(quantity_caps.values()), spec.quantity_step)
    min_quantity = _ceil_to_step(float(spec.min_quantity), spec.quantity_step)
    limiting_constraints = sorted(
        key for key, value in quantity_caps.items() if abs(value - quantity) <= 1e-12
    )

    if quantity + 1e-12 < min_quantity:
        return _no_plan(
            side=side,
            status="NO_OPTIONS_RISK_PLAN",
            reason="Configured budgets cannot support the platform minimum quantity.",
            diagnostics={
                "quantity_caps": quantity_caps,
                "limiting_constraints": limiting_constraints,
                "minimum_quantity": min_quantity,
            },
        )

    total_premium_outlay = quantity * premium_outlay_per_qty + spec.fixed_entry_cost_account
    total_planned_loss = (
        quantity * planned_loss_per_qty
        + spec.fixed_entry_cost_account
        + spec.fixed_stop_exit_cost_account
    )
    total_tail_loss = quantity * tail_loss_per_qty + spec.fixed_entry_cost_account
    total_net_reward = (
        quantity * net_reward_per_qty
        - spec.fixed_entry_cost_account
        - spec.fixed_target_exit_cost_account
    )
    net_rr = total_net_reward / total_planned_loss if total_planned_loss > 0 else 0.0

    if net_rr + 1e-12 < policy.min_net_reward_risk:
        return _no_plan(
            side=side,
            status="NO_OPTIONS_RISK_PLAN",
            reason="Net reward/risk after supplied costs is below the configured crypto Options threshold.",
            diagnostics={
                "quantity": quantity,
                "net_reward_risk": net_rr,
                "required_min_net_reward_risk": policy.min_net_reward_risk,
                "total_net_reward": total_net_reward,
                "total_planned_loss": total_planned_loss,
            },
        )

    tolerance = 1e-8
    if total_premium_outlay > premium_budget + tolerance:
        raise AssertionError("rounded quantity exceeded premium allocation budget")
    if total_planned_loss > planned_risk_budget + tolerance:
        raise AssertionError("rounded quantity exceeded planned risk budget")
    if total_tail_loss > tail_risk_budget + tolerance:
        raise AssertionError("rounded quantity exceeded tail risk budget")

    risk_plan = {
        "contract_symbol": selected.get("symbol"),
        "option_type": expected_option_type,
        "side_candidate": side,
        "entry_reference_ask": ask,
        "effective_entry_premium_after_slippage": effective_entry,
        "stop_premium": scenario.stop_premium,
        "effective_stop_exit_premium_after_slippage": effective_stop_exit,
        "target_premium": scenario.target_premium,
        "effective_target_exit_premium_after_slippage": effective_target_exit,
        "stop_basis": scenario.stop_basis,
        "target_basis": scenario.target_basis,
        "quantity": quantity,
        "quantity_step": spec.quantity_step,
        "minimum_quantity": min_quantity,
        "limiting_constraint": limiting_constraints[0] if limiting_constraints else None,
        "limiting_constraints": limiting_constraints,
        "quantity_caps": quantity_caps,
        "premium_budget": premium_budget,
        "planned_risk_budget": planned_risk_budget,
        "tail_risk_budget": tail_risk_budget,
        "premium_outlay": total_premium_outlay,
        "planned_stop_loss": total_planned_loss,
        "full_premium_tail_loss": total_tail_loss,
        "net_target_reward": total_net_reward,
        "net_reward_risk": net_rr,
        "planned_stop_is_guaranteed_max_loss": False,
        "tail_loss_models_full_premium_at_risk": True,
        "account_currency": spec.account_currency,
        "premium_currency": spec.premium_currency,
        "premium_to_account_rate": spec.premium_to_account_rate,
        "contract_multiplier": spec.contract_multiplier,
        "cost_model": {
            "entry_slippage_pct_of_premium": spec.entry_slippage_pct_of_premium,
            "exit_slippage_pct_of_premium": spec.exit_slippage_pct_of_premium,
            "entry_fee_per_quantity_account": spec.entry_fee_per_quantity_account,
            "stop_exit_fee_per_quantity_account": spec.stop_exit_fee_per_quantity_account,
            "target_exit_fee_per_quantity_account": spec.target_exit_fee_per_quantity_account,
            "fixed_entry_cost_account": spec.fixed_entry_cost_account,
            "fixed_stop_exit_cost_account": spec.fixed_stop_exit_cost_account,
            "fixed_target_exit_cost_account": spec.fixed_target_exit_cost_account,
        },
    }

    return {
        "version": "BTC_OPTIONS_RISK_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "OPTIONS_RISK_PLAN_READY",
        "side_candidate": side,
        "selected_contract_symbol": selected.get("symbol"),
        "risk_policy": asdict(policy),
        "execution_spec": asdict(spec),
        "scenario": asdict(scenario),
        "risk_plan": risk_plan,
        "quantity_selected": True,
        "trade_generated": False,
        "order_created": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_RISK_CONTRACT_V1",
        "default_platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "requires_selected_options_contract": True,
        "crypto_risk_policy_must_be_explicit": True,
        "inherits_commodity_15000_rule": False,
        "planned_stop_is_guaranteed_max_loss": False,
        "full_premium_tail_loss_checked_separately": True,
        "premium_allocation_checked": True,
        "planned_stop_risk_checked": True,
        "tail_risk_checked": True,
        "fees_and_slippage_are_explicit_inputs": True,
        "contract_multiplier_and_quantity_step_are_explicit_inputs": True,
        "stop_and_target_are_invented_here": False,
        "net_reward_risk_checked_after_costs": True,
        "quantity_selected_here": True,
        "trade_generated_here": False,
        "order_created_here": False,
        "futures_route_invoked": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
        "research_only": True,
    }
