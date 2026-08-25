from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from .risk_discipline import RiskDisciplineRequest, evaluate_risk_discipline


IST = ZoneInfo("Asia/Kolkata")
PROTOCOL_REVISION = "paper-trade-lifecycle-v1-2026-08-25"
MAX_LIVE_DECISION_AGE_SECONDS = 120


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExactOptionContract(StrictModel):
    symbol: str = Field(min_length=1, max_length=30)
    expiry: date
    strike: float = Field(gt=0)
    option_type: Literal["CE", "PE"]
    lot_size: int = Field(ge=1)


class PremiumObservation(StrictModel):
    provider: Literal["GROWW", "HISTORICAL_REPLAY"]
    data_status: Literal["LIVE", "HISTORICAL"]
    symbol: str = Field(min_length=1, max_length=30)
    expiry: date
    strike: float = Field(gt=0)
    option_type: Literal["CE", "PE"]
    premium_price: float = Field(gt=0)
    observed_at: datetime
    source_id: str = Field(min_length=1, max_length=160)


class PaperTrade(StrictModel):
    schema_version: Literal[1] = 1
    protocol_revision: str
    trade_id: str
    status: Literal["OPEN", "CLOSED"]
    paper_only: Literal[True] = True
    live_execution_enabled: Literal[False] = False
    order_endpoint_called: Literal[False] = False
    symbol: str
    expiry: date
    strike: float
    option_type: Literal["CE", "PE"]
    lot_size: int
    quantity: int = Field(ge=1)
    lots: int = Field(ge=1)
    correlation_group: str
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    estimated_cost_rupees: float
    initial_risk_rupees: float
    opened_at: datetime
    last_observed_at: datetime
    last_price: float
    min_premium: float
    max_premium: float
    mark_sequence: int
    unrealized_pnl_rupees: float
    realized_pnl_rupees: float | None = None
    exit_price: float | None = None
    closed_at: datetime | None = None
    exit_reason: Literal["STOP", "TARGET", "MANUAL"] | None = None
    r_multiple: float | None = None
    risk_decision_id: str
    last_source_id: str


class PaperTradeOpenRequest(StrictModel):
    risk_request: RiskDisciplineRequest
    contract: ExactOptionContract


class PaperTradeMarkRequest(StrictModel):
    paper_trade: PaperTrade
    manual_exit: bool = False


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _round(value: float) -> float:
    return round(float(value), 2)


def _in_nse_window(value: datetime) -> bool:
    ist = _utc(value).astimezone(IST)
    return ist.weekday() < 5 and time(9, 15) <= ist.time().replace(tzinfo=None) <= time(15, 30)


def _contract_matches(contract: ExactOptionContract, observation: PremiumObservation) -> bool:
    return (
        contract.symbol.strip().upper() == observation.symbol.strip().upper()
        and contract.expiry == observation.expiry
        and abs(contract.strike - observation.strike) < 1e-6
        and contract.option_type == observation.option_type
    )


def _decision_id(risk_request: RiskDisciplineRequest, observation: PremiumObservation) -> str:
    payload = {
        "risk_request": risk_request.model_dump(mode="json"),
        "observation": observation.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _trade_id(decision_id: str, contract: ExactOptionContract) -> str:
    payload = (
        decision_id
        + "|"
        + contract.symbol.strip().upper()
        + "|"
        + contract.expiry.isoformat()
        + "|"
        + str(round(contract.strike, 4))
        + "|"
        + contract.option_type
    )
    return "paper-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _open_projection(trade: PaperTrade) -> dict:
    return {
        "symbol": trade.symbol,
        "correlation_group": trade.correlation_group,
        "risk_rupees": trade.initial_risk_rupees,
        "current_value_rupees": _round(trade.last_price * trade.quantity),
    }


def _closed_projection(trade: PaperTrade) -> dict | None:
    if trade.status != "CLOSED" or trade.realized_pnl_rupees is None or trade.closed_at is None:
        return None
    return {
        "trade_id": trade.trade_id,
        "pnl_rupees": trade.realized_pnl_rupees,
        "closed_at": trade.closed_at.isoformat(),
        "verified_source": "GROWW_LIVE_OPTION_LTP",
    }


def open_paper_trade(
    risk_request: RiskDisciplineRequest,
    contract: ExactOptionContract,
    observation: PremiumObservation,
) -> dict:
    """Re-evaluate at an exact observed option premium and create a paper-only state."""

    blockers: list[str] = []
    proposed = risk_request.proposed_trade
    symbol = contract.symbol.strip().upper()
    observed_at = _utc(observation.observed_at)
    requested_at = _utc(risk_request.evaluated_at)

    if risk_request.mode != "PAPER":
        blockers.append("PAPER_MODE_REQUIRED")
    if proposed.symbol.strip().upper() != symbol:
        blockers.append("CONTRACT_SYMBOL_MISMATCH")
    if proposed.option_type != contract.option_type:
        blockers.append("CONTRACT_OPTION_TYPE_MISMATCH")
    if proposed.lot_size != contract.lot_size:
        blockers.append("CONTRACT_LOT_SIZE_MISMATCH")
    if not _contract_matches(contract, observation):
        blockers.append("OBSERVATION_CONTRACT_MISMATCH")
    if observation.provider == "GROWW" and observation.data_status != "LIVE":
        blockers.append("LIVE_GROWW_OPTION_DATA_REQUIRED")
    if observation.provider == "HISTORICAL_REPLAY" and observation.data_status != "HISTORICAL":
        blockers.append("HISTORICAL_REPLAY_STATUS_REQUIRED")
    if observation.data_status == "LIVE" and abs((observed_at - requested_at).total_seconds()) > MAX_LIVE_DECISION_AGE_SECONDS:
        blockers.append("RISK_DECISION_STALE")
    if observation.data_status == "LIVE" and not _in_nse_window(observed_at):
        blockers.append("NSE_SESSION_NOT_EXECUTABLE")
    if contract.expiry < observed_at.astimezone(IST).date():
        blockers.append("OPTION_CONTRACT_EXPIRED")

    refreshed_trade = proposed.model_copy(update={"entry_price": observation.premium_price})
    refreshed_request = risk_request.model_copy(
        update={
            "proposed_trade": refreshed_trade,
            "evaluated_at": observed_at,
        }
    )
    risk_decision = evaluate_risk_discipline(refreshed_request)
    blockers.extend(risk_decision["blockers"])

    if risk_decision["final_action"] != "PAPER_TRADE_ONLY":
        blockers.append("RISK_DECISION_NOT_APPROVED_FOR_PAPER")
    blockers = list(dict.fromkeys(blockers))

    if blockers:
        return {
            "schema_version": 1,
            "protocol_revision": PROTOCOL_REVISION,
            "status": "OPEN_BLOCKED",
            "paper_trade": None,
            "risk_decision": risk_decision,
            "blockers": blockers,
            "live_execution_enabled": False,
            "order_endpoint_called": False,
        }

    quantity = int(risk_decision["position_sizing"]["evaluated_quantity"])
    initial_risk = _round(
        max(0.0, observation.premium_price - proposed.stop_price) * quantity
        + proposed.estimated_cost_rupees
    )
    decision_id = _decision_id(refreshed_request, observation)
    trade = PaperTrade(
        protocol_revision=PROTOCOL_REVISION,
        trade_id=_trade_id(decision_id, contract),
        status="OPEN",
        symbol=symbol,
        expiry=contract.expiry,
        strike=contract.strike,
        option_type=contract.option_type,
        lot_size=contract.lot_size,
        quantity=quantity,
        lots=quantity // contract.lot_size,
        correlation_group=proposed.correlation_group.strip().upper(),
        entry_price=_round(observation.premium_price),
        stop_price=_round(proposed.stop_price),
        target_price=_round(proposed.target_price),
        estimated_cost_rupees=_round(proposed.estimated_cost_rupees),
        initial_risk_rupees=initial_risk,
        opened_at=observed_at,
        last_observed_at=observed_at,
        last_price=_round(observation.premium_price),
        min_premium=_round(observation.premium_price),
        max_premium=_round(observation.premium_price),
        mark_sequence=0,
        unrealized_pnl_rupees=_round(-proposed.estimated_cost_rupees),
        risk_decision_id=decision_id,
        last_source_id=observation.source_id,
    )
    return {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "OPENED_PAPER",
        "paper_trade": trade.model_dump(mode="json"),
        "risk_decision": risk_decision,
        "blockers": [],
        "open_position_risk": _open_projection(trade),
        "live_execution_enabled": False,
        "order_endpoint_called": False,
    }


def _validate_open_state(trade: PaperTrade) -> None:
    contract = ExactOptionContract(
        symbol=trade.symbol,
        expiry=trade.expiry,
        strike=trade.strike,
        option_type=trade.option_type,
        lot_size=trade.lot_size,
    )
    if trade.trade_id != _trade_id(trade.risk_decision_id, contract):
        raise ValueError("Paper trade identity check failed")
    if trade.quantity % trade.lot_size != 0 or trade.lots != trade.quantity // trade.lot_size:
        raise ValueError("Paper trade quantity is not a consistent whole-lot size")
    if not trade.stop_price < trade.entry_price < trade.target_price:
        raise ValueError("Paper trade price geometry is invalid")
    expected_risk = _round(
        (trade.entry_price - trade.stop_price) * trade.quantity
        + trade.estimated_cost_rupees
    )
    if abs(expected_risk - trade.initial_risk_rupees) > 0.01:
        raise ValueError("Paper trade initial risk check failed")
    if _utc(trade.last_observed_at) < _utc(trade.opened_at):
        raise ValueError("Paper trade observation order is invalid")


def mark_paper_trade(
    paper_trade: PaperTrade,
    observation: PremiumObservation,
    manual_exit: bool = False,
) -> dict:
    """Apply one verified premium observation. It never sends or simulates a broker order."""

    if paper_trade.status != "OPEN":
        raise ValueError("Only an OPEN paper trade can be marked")
    _validate_open_state(paper_trade)
    contract = ExactOptionContract(
        symbol=paper_trade.symbol,
        expiry=paper_trade.expiry,
        strike=paper_trade.strike,
        option_type=paper_trade.option_type,
        lot_size=paper_trade.lot_size,
    )
    if not _contract_matches(contract, observation):
        raise ValueError("Premium observation does not match the paper trade contract")
    if observation.provider != "GROWW" or observation.data_status != "LIVE":
        raise ValueError("Paper lifecycle marking requires LIVE Groww option data")

    observed_at = _utc(observation.observed_at)
    if not _in_nse_window(observed_at):
        raise ValueError("Paper lifecycle marking requires an executable NSE session")
    if observed_at < _utc(paper_trade.last_observed_at):
        return {
            "schema_version": 1,
            "protocol_revision": PROTOCOL_REVISION,
            "status": "IGNORED_OUT_OF_ORDER",
            "paper_trade": paper_trade.model_dump(mode="json"),
            "verified_closed_trade": None,
            "open_position_risk": _open_projection(paper_trade),
            "live_execution_enabled": False,
            "order_endpoint_called": False,
        }
    if observation.source_id == paper_trade.last_source_id:
        return {
            "schema_version": 1,
            "protocol_revision": PROTOCOL_REVISION,
            "status": "IGNORED_DUPLICATE",
            "paper_trade": paper_trade.model_dump(mode="json"),
            "verified_closed_trade": None,
            "open_position_risk": _open_projection(paper_trade),
            "live_execution_enabled": False,
            "order_endpoint_called": False,
        }

    price = _round(observation.premium_price)
    gross_pnl = (price - paper_trade.entry_price) * paper_trade.quantity
    unrealized = _round(gross_pnl - paper_trade.estimated_cost_rupees)
    exit_reason = None
    if manual_exit:
        exit_reason = "MANUAL"
    elif price <= paper_trade.stop_price:
        exit_reason = "STOP"
    elif price >= paper_trade.target_price:
        exit_reason = "TARGET"

    updates = {
        "last_observed_at": observed_at,
        "last_price": price,
        "min_premium": min(paper_trade.min_premium, price),
        "max_premium": max(paper_trade.max_premium, price),
        "mark_sequence": paper_trade.mark_sequence + 1,
        "unrealized_pnl_rupees": unrealized,
        "last_source_id": observation.source_id,
    }
    if exit_reason:
        realized = unrealized
        updates.update(
            {
                "status": "CLOSED",
                "realized_pnl_rupees": realized,
                "exit_price": price,
                "closed_at": observed_at,
                "exit_reason": exit_reason,
                "r_multiple": round(
                    realized / paper_trade.initial_risk_rupees,
                    3,
                )
                if paper_trade.initial_risk_rupees > 0
                else 0.0,
            }
        )

    marked = paper_trade.model_copy(update=updates)
    return {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "CLOSED_PAPER" if marked.status == "CLOSED" else "MARKED_OPEN",
        "paper_trade": marked.model_dump(mode="json"),
        "verified_closed_trade": _closed_projection(marked),
        "open_position_risk": _open_projection(marked) if marked.status == "OPEN" else None,
        "live_execution_enabled": False,
        "order_endpoint_called": False,
    }


async def fetch_live_option_observation(provider, contract: ExactOptionContract) -> PremiumObservation:
    chain = await provider.option_chain(contract.symbol.strip().upper(), contract.expiry.isoformat())
    if str(chain.get("provider", "")).upper() != "GROWW":
        raise ValueError("LIVE Groww option data is required for paper lifecycle")

    raw = chain.get("data", chain)
    payload = raw.get("payload", raw) if isinstance(raw, dict) else {}
    strikes = payload.get("strikes", {}) if isinstance(payload, dict) else {}
    selected = None
    for strike_key, row in strikes.items():
        try:
            strike = float(strike_key)
        except (TypeError, ValueError):
            continue
        if abs(strike - contract.strike) < 1e-6:
            selected = row
            break
    if not isinstance(selected, dict):
        raise ValueError("Exact option strike was not present in the live Groww chain")

    leg = selected.get(contract.option_type) or {}
    premium = leg.get("ltp")
    try:
        premium = float(premium)
    except (TypeError, ValueError):
        premium = 0.0
    if premium <= 0:
        raise ValueError("Exact option contract has no positive live premium")

    observed_at = datetime.now(timezone.utc)
    source_payload = (
        contract.symbol.strip().upper()
        + "|"
        + contract.expiry.isoformat()
        + "|"
        + str(round(contract.strike, 4))
        + "|"
        + contract.option_type
        + "|"
        + str(premium)
        + "|"
        + observed_at.isoformat()
    )
    return PremiumObservation(
        provider="GROWW",
        data_status="LIVE",
        symbol=contract.symbol.strip().upper(),
        expiry=contract.expiry,
        strike=contract.strike,
        option_type=contract.option_type,
        premium_price=premium,
        observed_at=observed_at,
        source_id="groww-chain-" + hashlib.sha256(source_payload.encode()).hexdigest()[:24],
    )
