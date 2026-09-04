from __future__ import annotations

from datetime import datetime
from math import floor

from .commodity_time import parse_ist_timestamp

MODEL_ID = "CRUDE_OIL_MINI_OPTION_EXPRESSION_V1"
MAX_CAPITAL_RUPEES = 15_000.0
TRADE_ACTIONS = {"BUY_CE": "CE", "BUY_PE": "PE"}


def _number(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError, OverflowError):
        return None


def _visible(row: dict, click: datetime) -> bool:
    for key in ("sample_bucket_at", "observed_at", "collected_at"):
        value = row.get(key)
        if value is None:
            return False
        try:
            if parse_ist_timestamp(value) > click:
                return False
        except Exception:
            return False
    return True


def build_option_expression(
    *,
    action: str,
    option_positioning: dict | None,
    click_at,
    max_capital_rupees: float = MAX_CAPITAL_RUPEES,
) -> dict | None:
    """Translate an existing Current Mind BUY decision into a PIT option contract.

    This layer never creates direction. WAIT/NO_TRADE remain unexpressed. It uses
    only the nearest expiry already selected by the PIT option snapshot and ranks
    eligible contracts by ATM distance, then liquidity (volume/OI).
    """
    option_type = TRADE_ACTIONS.get(str(action or "").upper())
    if option_type is None:
        return None

    click = parse_ist_timestamp(click_at)
    positioning = dict(option_positioning or {})
    if positioning.get("status") != "AVAILABLE":
        return {"status": "UNAVAILABLE", "model_id": MODEL_ID, "reason": "NO_PIT_OPTION_SNAPSHOT"}

    nearest_expiry = str(positioning.get("nearest_expiry") or "")
    underlying = _number(positioning.get("underlying_price"))
    candidates = []
    for row in positioning.get("contracts") or []:
        if str(row.get("option_type") or "").upper() != option_type:
            continue
        if nearest_expiry and str(row.get("expiry_date") or "") != nearest_expiry:
            continue
        if not _visible(row, click):
            continue
        premium = _number(row.get("ask_price")) or _number(row.get("last_price"))
        lot_size = _number(row.get("lot_size"))
        strike = _number(row.get("strike"))
        if premium is None or premium <= 0 or lot_size is None or lot_size <= 0 or strike is None:
            continue
        one_lot_cost = premium * lot_size
        if one_lot_cost > max_capital_rupees:
            continue
        volume = _number(row.get("volume")) or 0.0
        oi = _number(row.get("open_interest")) or 0.0
        distance = abs(strike - underlying) if underlying is not None else 0.0
        candidates.append((distance, -volume, -oi, strike, row, premium, int(lot_size), one_lot_cost))

    if not candidates:
        return {
            "status": "UNAVAILABLE",
            "model_id": MODEL_ID,
            "reason": "NO_ELIGIBLE_CONTRACT_WITHIN_CAPITAL_CAP",
            "option_type": option_type,
            "max_capital_rupees": max_capital_rupees,
        }

    candidates.sort(key=lambda item: item[:4])
    _, _, _, strike, row, premium, lot_size, one_lot_cost = candidates[0]
    lots = max(1, floor(max_capital_rupees / one_lot_cost))
    quantity = lots * lot_size
    estimated_premium_outlay = premium * quantity
    return {
        "status": "EXPRESSED",
        "model_id": MODEL_ID,
        "underlying": "CRUDEOILM",
        "action": str(action).upper(),
        "option_type": option_type,
        "trading_symbol": row.get("trading_symbol"),
        "expiry_date": row.get("expiry_date"),
        "strike": strike,
        "premium_reference": premium,
        "premium_reference_basis": "ASK_IF_AVAILABLE_ELSE_PIT_LAST_PRICE",
        "lot_size": lot_size,
        "lots": lots,
        "quantity": quantity,
        "estimated_premium_outlay": round(estimated_premium_outlay, 2),
        "max_capital_rupees": max_capital_rupees,
        "sample_bucket_at": row.get("sample_bucket_at"),
        "observed_at": row.get("observed_at"),
        "collected_at": row.get("collected_at"),
        "selection_policy": "NEAREST_EXPIRY_ATM_FIRST_THEN_VOLUME_OI",
        "point_in_time": True,
        "paper_signal_only": True,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
    }
