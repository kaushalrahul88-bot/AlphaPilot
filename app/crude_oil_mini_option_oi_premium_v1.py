from __future__ import annotations

from collections import Counter

MODEL_ID = "CRUDE_OIL_MINI_OPTION_OI_PREMIUM_INTERPRETATION_V1"
MODEL_STATUS = "REGISTERED"
DIRECTIONAL = {"BULLISH", "BEARISH"}


def _number(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError, OverflowError):
        return None


def registration_contract() -> dict:
    """Immutable preregistration contract for the first OI+premium causal rule.

    The rule is deliberately sign-based and threshold-free. It may be used only
    prospectively after registration. Historical results must not be used to
    change the mapping or eligibility rules in this version.
    """
    return {
        "model_id": MODEL_ID,
        "status": MODEL_STATUS,
        "version": 1,
        "instrument_scope": ["MCX_CRUDEOILM_OPTIONS"],
        "option_types": ["CE", "PE"],
        "inputs": [
            "matched_contract_open_interest_change_from_previous_pit_bucket",
            "matched_contract_premium_change_from_previous_pit_bucket",
        ],
        "previous_bucket_required": True,
        "same_contract_match_required": True,
        "same_expiry_scope_required": True,
        "raw_oi_alone_can_vote": False,
        "premium_alone_can_vote": False,
        "zero_change_can_vote": False,
        "magnitude_weighting": False,
        "performance_fitted_thresholds": False,
        "side_consensus_rule": "STRICT_MAJORITY_OF_USABLE_MATCHED_CONTRACTS",
        "cross_side_confirmation_rule": "CE_AND_PE_SIDE_DIRECTIONS_MUST_AGREE",
        "prospective_only": True,
        "retroactive_reconstruction_allowed": False,
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_brain_effect": "NONE",
        "promotion_allowed": False,
        "mapping": {
            "CE": {
                "OI_UP_PREMIUM_UP": {"state": "CALL_LONG_BUILDUP", "direction": "BULLISH"},
                "OI_UP_PREMIUM_DOWN": {"state": "CALL_WRITING", "direction": "BEARISH"},
                "OI_DOWN_PREMIUM_UP": {"state": "CALL_SHORT_COVERING", "direction": "BULLISH"},
                "OI_DOWN_PREMIUM_DOWN": {"state": "CALL_LONG_UNWINDING", "direction": "BEARISH"},
            },
            "PE": {
                "OI_UP_PREMIUM_UP": {"state": "PUT_LONG_BUILDUP", "direction": "BEARISH"},
                "OI_UP_PREMIUM_DOWN": {"state": "PUT_WRITING", "direction": "BULLISH"},
                "OI_DOWN_PREMIUM_UP": {"state": "PUT_SHORT_COVERING", "direction": "BEARISH"},
                "OI_DOWN_PREMIUM_DOWN": {"state": "PUT_LONG_UNWINDING", "direction": "BULLISH"},
            },
        },
    }


def _contract_interpretation(row: dict) -> dict | None:
    option_type = str(row.get("option_type") or "").upper()
    if option_type not in {"CE", "PE"}:
        return None

    oi_change = _number(row.get("oi_change_from_previous_bucket"))
    premium_change = _number(row.get("premium_change_from_previous_bucket"))
    if oi_change is None or premium_change is None or oi_change == 0 or premium_change == 0:
        return None

    oi_leg = "OI_UP" if oi_change > 0 else "OI_DOWN"
    premium_leg = "PREMIUM_UP" if premium_change > 0 else "PREMIUM_DOWN"
    key = f"{oi_leg}_{premium_leg}"
    mapped = registration_contract()["mapping"][option_type][key]
    return {
        "trading_symbol": row.get("trading_symbol"),
        "expiry_date": row.get("expiry_date"),
        "strike": row.get("strike"),
        "option_type": option_type,
        "oi_change_from_previous_bucket": oi_change,
        "premium_change_from_previous_bucket": premium_change,
        "state": mapped["state"],
        "direction": mapped["direction"],
    }


def _side_consensus(signals: list[dict], option_type: str) -> dict:
    side = [row for row in signals if row.get("option_type") == option_type]
    counts = Counter(row["direction"] for row in side if row.get("direction") in DIRECTIONAL)
    bullish = int(counts.get("BULLISH", 0))
    bearish = int(counts.get("BEARISH", 0))
    if bullish == bearish:
        direction = "UNKNOWN"
        status = "NO_STRICT_MAJORITY" if side else "NO_USABLE_MATCHED_CONTRACTS"
    else:
        direction = "BULLISH" if bullish > bearish else "BEARISH"
        status = "STRICT_MAJORITY"
    return {
        "option_type": option_type,
        "usable_contracts": len(side),
        "bullish_contracts": bullish,
        "bearish_contracts": bearish,
        "direction": direction,
        "status": status,
    }


def interpret_option_oi_premium(
    contracts: list[dict] | None,
    *,
    previous_sample_bucket_at=None,
) -> dict:
    """Interpret matched PIT option OI and premium changes under the registered rule."""
    registration = registration_contract()
    rows = list(contracts or [])
    if not previous_sample_bucket_at:
        return {
            "model_id": MODEL_ID,
            "registration": registration,
            "status": "INSUFFICIENT_PIT_BUCKETS",
            "direction": "UNKNOWN",
            "counts_for_direction": False,
            "signals": [],
            "ce": _side_consensus([], "CE"),
            "pe": _side_consensus([], "PE"),
        }

    signals = [signal for row in rows if (signal := _contract_interpretation(row)) is not None]
    ce = _side_consensus(signals, "CE")
    pe = _side_consensus(signals, "PE")
    ce_direction = ce["direction"]
    pe_direction = pe["direction"]

    if ce_direction not in DIRECTIONAL or pe_direction not in DIRECTIONAL:
        status = "INSUFFICIENT_TWO_SIDED_CONSENSUS"
        direction = "UNKNOWN"
        counts_for_direction = False
    elif ce_direction != pe_direction:
        status = "CROSS_SIDE_CONFLICT"
        direction = "UNKNOWN"
        counts_for_direction = False
    else:
        status = "COHERENT_TWO_SIDED_CONFIRMATION"
        direction = ce_direction
        counts_for_direction = True

    return {
        "model_id": MODEL_ID,
        "registration": registration,
        "status": status,
        "direction": direction,
        "counts_for_direction": counts_for_direction,
        "previous_sample_bucket_at": str(previous_sample_bucket_at),
        "usable_contracts": len(signals),
        "signals": signals,
        "ce": ce,
        "pe": pe,
        "guardrail": "A directional vote exists only when independently summarized CE and PE flows agree under this preregistered mapping.",
    }
