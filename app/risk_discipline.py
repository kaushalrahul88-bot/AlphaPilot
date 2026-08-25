from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import floor
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


IST = ZoneInfo("Asia/Kolkata")
PROTOCOL_REVISION = "portfolio-risk-discipline-v1-2026-08-25"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskPolicy(StrictModel):
    """Configurable limits bounded by AlphaPilot's frozen v1 safety caps."""

    max_risk_per_trade_pct: float = Field(1.0, gt=0, le=1.0)
    max_daily_loss_pct: float = Field(3.0, gt=0, le=3.0)
    max_weekly_loss_pct: float = Field(6.0, gt=0, le=6.0)
    max_open_risk_pct: float = Field(3.0, gt=0, le=6.0)
    max_correlated_risk_pct: float = Field(1.0, gt=0, le=2.0)
    max_position_value_pct: float = Field(20.0, gt=0, le=30.0)
    max_gross_exposure_pct: float = Field(50.0, gt=0, le=100.0)
    max_concurrent_positions: int = Field(2, ge=1, le=5)
    max_consecutive_losses: int = Field(3, ge=1, le=5)
    loss_cooldown_minutes: int = Field(60, ge=15, le=375)
    minimum_risk_reward: float = Field(1.5, ge=1.5, le=5.0)
    max_drawdown_pct: float = Field(8.0, gt=0, le=10.0)


class OperationalGates(StrictModel):
    account_state_verified: bool
    executable_nse_session: bool
    fresh_intraday_candles: bool
    universe_scan_complete: bool
    fno_confirmation_complete: bool
    quality_checks_complete: bool
    liquidity_passed: bool


class ProposedTrade(StrictModel):
    symbol: str = Field(min_length=1, max_length=30)
    option_type: Literal["CE", "PE"]
    correlation_group: str = Field(min_length=1, max_length=40)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    lot_size: int = Field(ge=1)
    estimated_cost_rupees: float = Field(0.0, ge=0)
    requested_quantity: int | None = Field(None, ge=1)


class OpenPositionRisk(StrictModel):
    symbol: str = Field(min_length=1, max_length=30)
    correlation_group: str = Field(min_length=1, max_length=40)
    risk_rupees: float = Field(ge=0)
    current_value_rupees: float = Field(ge=0)


class ClosedTrade(StrictModel):
    pnl_rupees: float
    closed_at: datetime


class ControlledLiveEvidence(StrictModel):
    paper_trades: int = Field(0, ge=0)
    clean_paper_sessions: int = Field(0, ge=0)
    expectancy_r: float = 0.0
    profit_factor: float = Field(0.0, ge=0)
    max_drawdown_r: float = Field(0.0, ge=0)
    manual_approval_recorded: bool = False


class RiskDisciplineRequest(StrictModel):
    mode: Literal["PAPER", "CONTROLLED_LIVE_PREVIEW"] = "PAPER"
    capital_rupees: float = Field(gt=0)
    proposed_trade: ProposedTrade
    operational_gates: OperationalGates
    open_positions: list[OpenPositionRisk] = Field(default_factory=list)
    closed_trades: list[ClosedTrade] = Field(default_factory=list)
    controlled_live_evidence: ControlledLiveEvidence = Field(default_factory=ControlledLiveEvidence)
    policy: RiskPolicy = Field(default_factory=RiskPolicy)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _round(value: float) -> float:
    return round(float(value), 2)


def _in_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def _whole_lot_quantity(quantity: float, lot_size: int) -> int:
    return max(0, floor(quantity / lot_size) * lot_size)


def _loss_state(closed_trades: list[ClosedTrade], evaluated_at: datetime, capital: float) -> dict:
    now = _in_ist(evaluated_at)
    ordered = sorted(
        (trade for trade in closed_trades if _in_ist(trade.closed_at) <= now),
        key=lambda trade: _in_ist(trade.closed_at),
    )
    week_start = now.date() - timedelta(days=now.weekday())
    daily_pnl = sum(trade.pnl_rupees for trade in ordered if _in_ist(trade.closed_at).date() == now.date())
    weekly_pnl = sum(trade.pnl_rupees for trade in ordered if _in_ist(trade.closed_at).date() >= week_start)

    consecutive_losses = 0
    last_loss_at: datetime | None = None
    for trade in reversed(ordered):
        if trade.pnl_rupees < 0:
            consecutive_losses += 1
            if last_loss_at is None:
                last_loss_at = _in_ist(trade.closed_at)
        else:
            break

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in ordered:
        equity += trade.pnl_rupees
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    return {
        "daily_pnl_rupees": _round(daily_pnl),
        "weekly_pnl_rupees": _round(weekly_pnl),
        "daily_loss_rupees": _round(max(0.0, -daily_pnl)),
        "weekly_loss_rupees": _round(max(0.0, -weekly_pnl)),
        "consecutive_losses": consecutive_losses,
        "last_loss_at": last_loss_at,
        "max_drawdown_rupees": _round(max_drawdown),
        "max_drawdown_pct": _round(max_drawdown / capital * 100.0),
    }


def _arming_checks(evidence: ControlledLiveEvidence) -> list[dict]:
    checks = [
        ("MIN_30_PAPER_TRADES", evidence.paper_trades >= 30, evidence.paper_trades, ">= 30"),
        ("MIN_10_CLEAN_PAPER_SESSIONS", evidence.clean_paper_sessions >= 10, evidence.clean_paper_sessions, ">= 10"),
        ("POSITIVE_PAPER_EXPECTANCY", evidence.expectancy_r > 0, evidence.expectancy_r, "> 0R"),
        ("MIN_1_20_PROFIT_FACTOR", evidence.profit_factor >= 1.2, evidence.profit_factor, ">= 1.20"),
        ("MAX_6R_PAPER_DRAWDOWN", evidence.max_drawdown_r <= 6.0, evidence.max_drawdown_r, "<= 6R"),
        ("MANUAL_APPROVAL_RECORDED", evidence.manual_approval_recorded, evidence.manual_approval_recorded, "true"),
    ]
    return [
        {"code": code, "passed": bool(passed), "observed": observed, "required": required}
        for code, passed, observed, required in checks
    ]


def evaluate_risk_discipline(request: RiskDisciplineRequest) -> dict:
    """Return a deterministic paper/control-preview decision. This function never sends an order."""

    policy = request.policy
    trade = request.proposed_trade
    capital = request.capital_rupees
    now = _in_ist(request.evaluated_at)
    loss = _loss_state(request.closed_trades, request.evaluated_at, capital)

    open_risk = sum(position.risk_rupees for position in request.open_positions)
    open_value = sum(position.current_value_rupees for position in request.open_positions)
    correlated_risk = sum(
        position.risk_rupees
        for position in request.open_positions
        if position.correlation_group.strip().upper() == trade.correlation_group.strip().upper()
    )

    limits = {
        "per_trade": capital * policy.max_risk_per_trade_pct / 100.0,
        "daily_loss": capital * policy.max_daily_loss_pct / 100.0,
        "weekly_loss": capital * policy.max_weekly_loss_pct / 100.0,
        "open_risk": capital * policy.max_open_risk_pct / 100.0,
        "correlated_risk": capital * policy.max_correlated_risk_pct / 100.0,
        "position_value": capital * policy.max_position_value_pct / 100.0,
        "gross_exposure": capital * policy.max_gross_exposure_pct / 100.0,
    }
    remaining = {
        "daily_loss": max(0.0, limits["daily_loss"] - loss["daily_loss_rupees"]),
        "weekly_loss": max(0.0, limits["weekly_loss"] - loss["weekly_loss_rupees"]),
        "open_risk": max(0.0, limits["open_risk"] - open_risk),
        "correlated_risk": max(0.0, limits["correlated_risk"] - correlated_risk),
        "gross_exposure": max(0.0, limits["gross_exposure"] - open_value),
    }
    risk_budget = min(limits["per_trade"], remaining["daily_loss"], remaining["weekly_loss"], remaining["open_risk"], remaining["correlated_risk"])

    risk_per_unit = trade.entry_price - trade.stop_price
    reward_per_unit = trade.target_price - trade.entry_price
    blockers: list[str] = []

    if risk_per_unit <= 0:
        blockers.append("INVALID_LONG_OPTION_STOP_GEOMETRY")
    if reward_per_unit <= 0:
        blockers.append("INVALID_LONG_OPTION_TARGET_GEOMETRY")

    gate_codes = {
        "account_state_verified": "ACCOUNT_STATE_UNVERIFIED",
        "executable_nse_session": "NSE_SESSION_NOT_EXECUTABLE",
        "fresh_intraday_candles": "STALE_INTRADAY_CANDLES",
        "universe_scan_complete": "UNIVERSE_SCAN_INCOMPLETE",
        "fno_confirmation_complete": "FNO_CONFIRMATION_INCOMPLETE",
        "quality_checks_complete": "QUALITY_CHECKS_INCOMPLETE",
        "liquidity_passed": "LIQUIDITY_GATE_FAILED",
    }
    for field, code in gate_codes.items():
        if not getattr(request.operational_gates, field):
            blockers.append(code)

    if loss["daily_loss_rupees"] >= limits["daily_loss"]:
        blockers.append("DAILY_LOSS_LOCKED")
    if loss["weekly_loss_rupees"] >= limits["weekly_loss"]:
        blockers.append("WEEKLY_LOSS_LOCKED")
    if loss["max_drawdown_pct"] >= policy.max_drawdown_pct:
        blockers.append("MAX_DRAWDOWN_LOCKED")
    if len(request.open_positions) >= policy.max_concurrent_positions:
        blockers.append("MAX_CONCURRENT_POSITIONS_REACHED")
    if any(position.current_value_rupees > 0 and position.risk_rupees <= 0 for position in request.open_positions):
        blockers.append("OPEN_POSITION_RISK_UNDEFINED")
    if open_risk >= limits["open_risk"]:
        blockers.append("MAX_OPEN_RISK_REACHED")
    if correlated_risk >= limits["correlated_risk"]:
        blockers.append("MAX_CORRELATED_RISK_REACHED")
    if open_value >= limits["gross_exposure"]:
        blockers.append("MAX_GROSS_EXPOSURE_REACHED")

    cooldown_until: datetime | None = None
    if loss["consecutive_losses"] >= policy.max_consecutive_losses and loss["last_loss_at"]:
        cooldown_until = loss["last_loss_at"] + timedelta(minutes=policy.loss_cooldown_minutes)
        if now < cooldown_until:
            blockers.append("CONSECUTIVE_LOSS_COOLDOWN_ACTIVE")

    risk_quantity = 0
    if risk_per_unit > 0 and risk_budget > trade.estimated_cost_rupees:
        risk_quantity = _whole_lot_quantity(
            (risk_budget - trade.estimated_cost_rupees) / risk_per_unit,
            trade.lot_size,
        )
    position_value_quantity = _whole_lot_quantity(limits["position_value"] / trade.entry_price, trade.lot_size)
    gross_exposure_quantity = _whole_lot_quantity(remaining["gross_exposure"] / trade.entry_price, trade.lot_size)
    max_quantity = min(risk_quantity, position_value_quantity, gross_exposure_quantity)

    if max_quantity <= 0:
        blockers.append("NO_WHOLE_LOT_WITHIN_LIMITS")

    evaluated_quantity = max_quantity
    if trade.requested_quantity is not None:
        if trade.requested_quantity % trade.lot_size != 0:
            blockers.append("REQUESTED_QUANTITY_NOT_WHOLE_LOTS")
        if trade.requested_quantity > max_quantity:
            blockers.append("REQUESTED_QUANTITY_EXCEEDS_MAX")
        evaluated_quantity = trade.requested_quantity

    potential_loss = max(0.0, evaluated_quantity * max(0.0, risk_per_unit) + trade.estimated_cost_rupees)
    potential_profit = max(0.0, evaluated_quantity * max(0.0, reward_per_unit) - trade.estimated_cost_rupees)
    net_risk_reward = potential_profit / potential_loss if potential_loss > 0 else 0.0
    if net_risk_reward < policy.minimum_risk_reward:
        blockers.append("MINIMUM_RISK_REWARD_NOT_MET")
    if potential_loss > risk_budget + 1e-9:
        blockers.append("PROPOSED_RISK_EXCEEDS_AVAILABLE_BUDGET")

    arming_checks = _arming_checks(request.controlled_live_evidence)
    controlled_live_preview_eligible = not blockers and all(check["passed"] for check in arming_checks)
    live_execution_enabled = False

    if request.mode == "CONTROLLED_LIVE_PREVIEW":
        for check in arming_checks:
            if not check["passed"]:
                blockers.append(f"ARMING_{check['code']}_FAILED")
        blockers.append("LIVE_EXECUTION_DISABLED_V1")

    blockers = list(dict.fromkeys(blockers))
    paper_allowed = request.mode == "PAPER" and not blockers
    final_action = "PAPER_TRADE_ONLY" if paper_allowed else "NO_TRADE"

    return {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "evaluated_at": now.isoformat(),
        "mode": request.mode,
        "decision": "ALLOW_PAPER" if paper_allowed else "BLOCK",
        "final_action": final_action,
        "live_execution_enabled": live_execution_enabled,
        "controlled_live_preview_eligible": controlled_live_preview_eligible,
        "blockers": blockers,
        "position_sizing": {
            "symbol": trade.symbol.strip().upper(),
            "option_type": trade.option_type,
            "correlation_group": trade.correlation_group.strip().upper(),
            "lot_size": trade.lot_size,
            "max_quantity": max_quantity,
            "max_lots": max_quantity // trade.lot_size,
            "evaluated_quantity": evaluated_quantity,
            "risk_per_unit_rupees": _round(max(0.0, risk_per_unit)),
            "reward_per_unit_rupees": _round(max(0.0, reward_per_unit)),
            "position_value_rupees": _round(evaluated_quantity * trade.entry_price),
            "potential_loss_rupees": _round(potential_loss),
            "potential_profit_rupees": _round(potential_profit),
            "net_risk_reward": round(net_risk_reward, 3),
        },
        "risk_state": {
            **{key: value for key, value in loss.items() if key != "last_loss_at"},
            "open_positions": len(request.open_positions),
            "open_risk_rupees": _round(open_risk),
            "open_risk_pct": _round(open_risk / capital * 100.0),
            "correlated_risk_rupees": _round(correlated_risk),
            "gross_exposure_rupees": _round(open_value),
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        },
        "budgets": {
            "per_trade_limit_rupees": _round(limits["per_trade"]),
            "available_trade_risk_rupees": _round(risk_budget),
            "daily_loss_limit_rupees": _round(limits["daily_loss"]),
            "weekly_loss_limit_rupees": _round(limits["weekly_loss"]),
            "open_risk_limit_rupees": _round(limits["open_risk"]),
            "correlated_risk_limit_rupees": _round(limits["correlated_risk"]),
            "position_value_limit_rupees": _round(limits["position_value"]),
            "gross_exposure_limit_rupees": _round(limits["gross_exposure"]),
        },
        "arming_checks": arming_checks,
        "explanation_scope": "DETERMINISTIC_OUTPUT_ONLY",
    }
