"""Shadow-only F&O Market Brain V2 foundation.

The module separates point-in-time Perception, strictly-prior Experience/Memory,
and Decision/Discipline.  It never places orders, commits capital, or promotes a
strategy.  Outcomes may be attached to *prior* memory cases only when they were
already available before the current decision timestamp; outcomes never affect
analogue ranking.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MODEL_ID = "FNO_MARKET_BRAIN_V2_SHADOW"
ALLOWED_RESEARCH_ACTIONS = {"BUY_CE", "BUY_PE", "NO_TRADE"}


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("point-in-time timestamp is required")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("point-in-time timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _chain_payload(raw: Any) -> dict[str, Any]:
    """Unwrap Groww's stored wrapper without assuming one exact nesting depth."""
    current = _json_object(raw)
    for key in ("data", "payload", "payload"):
        child = current.get(key)
        if isinstance(child, Mapping):
            current = dict(child)
    return current


def _leg(strike_payload: Mapping[str, Any], option_type: str) -> dict[str, Any]:
    source = strike_payload.get(option_type) or {}
    if not isinstance(source, Mapping):
        source = {}
    greeks = source.get("greeks") or {}
    if not isinstance(greeks, Mapping):
        greeks = {}
    bid = source.get("best_bid", source.get("bid", source.get("bid_price")))
    ask = source.get("best_ask", source.get("ask", source.get("ask_price")))
    return {
        "option_type": option_type,
        "trading_symbol": source.get("trading_symbol"),
        "ltp": _number(source.get("ltp")),
        "open_interest": _number(source.get("open_interest")),
        "volume": _number(source.get("volume")),
        "iv": _number(greeks.get("iv")),
        "delta": _number(greeks.get("delta")),
        "gamma": _number(greeks.get("gamma")),
        "theta": _number(greeks.get("theta")),
        "vega": _number(greeks.get("vega")),
        "rho": _number(greeks.get("rho")),
        "best_bid": _number(bid),
        "best_ask": _number(ask),
    }


def _pcr_label(pcr: float | None) -> str:
    if pcr is None:
        return "PCR_UNKNOWN"
    if pcr < 0.9:
        return "PCR_CALL_HEAVY"
    if pcr <= 1.3:
        return "PCR_BALANCED"
    return "PCR_PUT_HEAVY"


def _market_phase(at: datetime) -> str:
    local = at.astimezone(IST)
    if local.weekday() >= 5:
        return "CLOSED"
    current = local.time()
    if time(9, 15) <= current < time(15, 15):
        return "CONTINUOUS"
    if time(15, 15) <= current <= time(15, 35):
        return "CLOSING_AUCTION"
    if time(15, 35) < current <= time(15, 40):
        return "FNO_ONLY_CLOSE"
    return "CLOSED"


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _presence_ratio(legs: Sequence[Mapping[str, Any]], key: str) -> float:
    if not legs:
        return 0.0
    return round(sum(leg.get(key) is not None for leg in legs) / len(legs), 6)


def build_perception(
    snapshot: Mapping[str, Any],
    *,
    decision_at: datetime | str,
    technical: Mapping[str, Any] | None = None,
    external_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable, outcome-blind F&O perception from a saved chain row."""
    observed_at = _timestamp(snapshot.get("observed_at"))
    as_of = _timestamp(decision_at)
    if observed_at > as_of:
        raise ValueError("future option-chain snapshot cannot enter perception")

    raw = _json_object(snapshot.get("payload"))
    chain = _chain_payload(raw)
    strikes = chain.get("strikes") or {}
    if not isinstance(strikes, Mapping):
        strikes = {}
    spot = _number(chain.get("underlying_ltp"))

    rows: list[dict[str, Any]] = []
    legs: list[dict[str, Any]] = []
    for strike_key, strike_payload in strikes.items():
        strike = _number(strike_key)
        if strike is None or not isinstance(strike_payload, Mapping):
            continue
        ce = _leg(strike_payload, "CE")
        pe = _leg(strike_payload, "PE")
        rows.append({"strike": strike, "CE": ce, "PE": pe})
        legs.extend((ce, pe))

    total_call_oi = sum(leg["open_interest"] or 0.0 for row in rows for leg in (row["CE"],))
    total_put_oi = sum(leg["open_interest"] or 0.0 for row in rows for leg in (row["PE"],))
    total_call_volume = sum(leg["volume"] or 0.0 for row in rows for leg in (row["CE"],))
    total_put_volume = sum(leg["volume"] or 0.0 for row in rows for leg in (row["PE"],))
    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else None

    atm_row = min(rows, key=lambda row: abs(row["strike"] - spot)) if rows and spot is not None else None
    call_wall = max(rows, key=lambda row: row["CE"].get("open_interest") or 0.0, default=None)
    put_wall = max(rows, key=lambda row: row["PE"].get("open_interest") or 0.0, default=None)

    atm_iv_values = []
    if atm_row:
        for side in ("CE", "PE"):
            value = atm_row[side].get("iv")
            if value is not None:
                atm_iv_values.append(value)
    atm_iv = sum(atm_iv_values) / len(atm_iv_values) if atm_iv_values else None

    technical_payload = dict(technical or {})
    external_payload = dict(external_context or {})
    labels = [_pcr_label(pcr)]
    if total_put_oi > total_call_oi:
        labels.append("PUT_OI_DOMINANT")
    elif total_call_oi > total_put_oi:
        labels.append("CALL_OI_DOMINANT")
    else:
        labels.append("OI_BALANCED_OR_EMPTY")
    if spot is not None and call_wall and put_wall:
        put_strike = put_wall["strike"]
        call_strike = call_wall["strike"]
        if put_strike < spot < call_strike:
            labels.append("SPOT_BETWEEN_OI_WALLS")
        if spot >= call_strike:
            labels.append("SPOT_AT_OR_ABOVE_CALL_WALL")
        if spot <= put_strike:
            labels.append("SPOT_AT_OR_BELOW_PUT_WALL")
    direction = str(technical_payload.get("direction") or "").upper()
    if direction in {"LONG", "SHORT"}:
        labels.append(f"TECHNICAL_{direction}")

    quality = {
        "strike_count": len(rows),
        "leg_count": len(legs),
        "underlying_ltp_present": spot is not None and spot > 0,
        "ltp_presence_ratio": _presence_ratio(legs, "ltp"),
        "oi_presence_ratio": _presence_ratio(legs, "open_interest"),
        "volume_presence_ratio": _presence_ratio(legs, "volume"),
        "iv_presence_ratio": _presence_ratio(legs, "iv"),
        "delta_presence_ratio": _presence_ratio(legs, "delta"),
        "bid_presence_ratio": _presence_ratio(legs, "best_bid"),
        "ask_presence_ratio": _presence_ratio(legs, "best_ask"),
    }
    quality["research_complete"] = bool(
        quality["underlying_ltp_present"]
        and quality["strike_count"] > 0
        and quality["ltp_presence_ratio"] == 1.0
        and quality["oi_presence_ratio"] == 1.0
        and quality["volume_presence_ratio"] == 1.0
        and quality["iv_presence_ratio"] == 1.0
        and quality["delta_presence_ratio"] == 1.0
    )
    quality["execution_quote_complete"] = bool(
        quality["research_complete"]
        and quality["bid_presence_ratio"] == 1.0
        and quality["ask_presence_ratio"] == 1.0
    )

    symbol = str(snapshot.get("underlying_symbol") or raw.get("symbol") or "").upper()
    expiry = str(snapshot.get("expiry_date") or raw.get("expiry") or "")[:10]
    dte = None
    try:
        if expiry:
            dte = (date.fromisoformat(expiry) - as_of.astimezone(IST).date()).days
    except ValueError:
        pass

    source_identity = {
        "provider": str(snapshot.get("provider") or raw.get("provider") or "GROWW"),
        "underlying_symbol": symbol,
        "expiry_date": expiry,
        "observed_at": observed_at.isoformat(),
    }
    fingerprint_input = {
        **source_identity,
        "spot": spot,
        "pcr_oi": pcr,
        "atm_strike": atm_row["strike"] if atm_row else None,
        "atm_iv": atm_iv,
        "call_wall": call_wall["strike"] if call_wall else None,
        "put_wall": put_wall["strike"] if put_wall else None,
        "technical_status": technical_payload.get("status"),
        "technical_direction": technical_payload.get("direction"),
        "labels": labels,
    }

    return {
        "model_id": MODEL_ID,
        "perception_fingerprint": _fingerprint(fingerprint_input),
        "decision_at": as_of.isoformat(),
        "observed_at": observed_at.isoformat(),
        "age_seconds": round((as_of - observed_at).total_seconds(), 3),
        "market_phase": _market_phase(as_of),
        "source": source_identity,
        "underlying": {"symbol": symbol, "ltp": spot},
        "derivatives": {
            "expiry_date": expiry or None,
            "days_to_expiry": dte,
            "pcr_oi": round(pcr, 6) if pcr is not None else None,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "total_call_volume": total_call_volume,
            "total_put_volume": total_put_volume,
            "call_wall_strike": call_wall["strike"] if call_wall else None,
            "put_wall_strike": put_wall["strike"] if put_wall else None,
            "atm_strike": atm_row["strike"] if atm_row else None,
            "atm_iv": round(atm_iv, 6) if atm_iv is not None else None,
        },
        "option_candidates": {
            "ATM_CE": dict(atm_row["CE"], strike=atm_row["strike"]) if atm_row else None,
            "ATM_PE": dict(atm_row["PE"], strike=atm_row["strike"]) if atm_row else None,
        },
        "technical": technical_payload,
        "external_context": external_payload,
        "quality": quality,
        "memory_features": {
            "regime_labels": sorted(set(labels)),
            "pcr_oi": pcr,
            "atm_iv": atm_iv,
            "technical_direction": direction if direction in {"LONG", "SHORT"} else None,
            "symbol": symbol,
        },
        "outcomes_used": False,
        "future_rows_allowed": False,
    }


def _case_perception(case: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = case.get("perception")
    return nested if isinstance(nested, Mapping) else case


def _similarity(current: Mapping[str, Any], candidate: Mapping[str, Any]) -> float:
    a = current.get("memory_features") or {}
    b = candidate.get("memory_features") or {}
    labels_a = set(a.get("regime_labels") or [])
    labels_b = set(b.get("regime_labels") or [])
    union = labels_a | labels_b
    label_score = len(labels_a & labels_b) / len(union) if union else 0.0
    same_symbol = 1.0 if a.get("symbol") and a.get("symbol") == b.get("symbol") else 0.0
    same_direction = 1.0 if a.get("technical_direction") and a.get("technical_direction") == b.get("technical_direction") else 0.0

    numeric_scores = []
    for key, scale in (("pcr_oi", 1.0), ("atm_iv", 50.0)):
        x = _number(a.get(key))
        y = _number(b.get(key))
        if x is not None and y is not None:
            numeric_scores.append(max(0.0, 1.0 - abs(x - y) / scale))
    numeric = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0.0
    return round(0.55 * label_score + 0.20 * same_symbol + 0.10 * same_direction + 0.15 * numeric, 6)


def build_experience_memory(
    current: Mapping[str, Any],
    prior_cases: Iterable[Mapping[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Retrieve strictly-prior analogues; outcomes never participate in ranking."""
    current_at = _timestamp(current.get("observed_at"))
    eligible: list[tuple[float, Mapping[str, Any], Mapping[str, Any], Any]] = []
    prior_perceptions = 0
    knowable_outcomes = 0

    for raw_case in prior_cases:
        perception = _case_perception(raw_case)
        try:
            case_at = _timestamp(perception.get("observed_at"))
        except ValueError:
            continue
        if case_at >= current_at:
            continue
        prior_perceptions += 1

        outcome = raw_case.get("outcome") if isinstance(raw_case, Mapping) else None
        available = raw_case.get("outcome_available_at") if isinstance(raw_case, Mapping) else None
        knowable_outcome = None
        if outcome is not None and available not in (None, ""):
            try:
                available_at = _timestamp(available)
            except ValueError:
                available_at = None
            if available_at is not None and available_at < current_at:
                knowable_outcome = outcome
                knowable_outcomes += 1

        score = _similarity(current, perception)
        if score > 0:
            eligible.append((score, perception, raw_case, knowable_outcome))

    eligible.sort(key=lambda item: (item[0], str(item[1].get("observed_at"))), reverse=True)
    selected = eligible[: max(0, int(limit))]
    analogues = []
    for score, perception, raw_case, outcome in selected:
        analogues.append({
            "perception_fingerprint": perception.get("perception_fingerprint"),
            "observed_at": perception.get("observed_at"),
            "symbol": (perception.get("underlying") or {}).get("symbol"),
            "similarity_score": score,
            "research_action": raw_case.get("research_action") if isinstance(raw_case, Mapping) else None,
            "outcome": outcome,
        })

    return {
        "status": "ANALOGUES_AVAILABLE" if analogues else "COLLECTING",
        "model_id": "FNO_EXPERIENCE_MEMORY_V2_SHADOW",
        "current_perception_fingerprint": current.get("perception_fingerprint"),
        "strictly_prior_required": True,
        "same_timestamp_allowed": False,
        "outcome_available_before_current_required": True,
        "outcome_used_for_similarity_ranking": False,
        "prior_perceptions": prior_perceptions,
        "prior_knowable_outcomes": knowable_outcomes,
        "analogues_used": len(analogues),
        "analogues": analogues,
        "decision_effect": "DESCRIPTIVE_ONLY_UNTIL_PROSPECTIVE_VALIDATED",
        "promotion_eligible": False,
    }


def decide_shadow(
    perception: Mapping[str, Any],
    memory: Mapping[str, Any] | None = None,
    *,
    max_snapshot_age_seconds: int = 300,
) -> dict[str, Any]:
    """Apply research discipline without enabling execution or capital deployment."""
    if max_snapshot_age_seconds < 0:
        raise ValueError("max_snapshot_age_seconds must be non-negative")

    technical = perception.get("technical") or {}
    status = str(technical.get("status") or "").upper()
    direction = str(technical.get("direction") or "").upper()
    quality = perception.get("quality") or {}
    phase = perception.get("market_phase")
    age = _number(perception.get("age_seconds"))

    blockers: list[str] = []
    warnings: list[str] = []
    if phase != "CONTINUOUS":
        blockers.append(f"MARKET_PHASE_{phase or 'UNKNOWN'}")
    if age is None or age < 0 or age > max_snapshot_age_seconds:
        blockers.append("OPTION_CHAIN_NOT_FRESH_ENOUGH")
    if not quality.get("research_complete"):
        blockers.append("PERCEPTION_DATA_INCOMPLETE")
    if status != "SETUP" or direction not in {"LONG", "SHORT"}:
        blockers.append("NO_CONFIRMED_TECHNICAL_SETUP")

    candidate_key = "ATM_CE" if direction == "LONG" else "ATM_PE" if direction == "SHORT" else None
    candidate = (perception.get("option_candidates") or {}).get(candidate_key) if candidate_key else None
    if candidate_key:
        if not isinstance(candidate, Mapping) or (_number(candidate.get("ltp")) or 0) <= 0:
            blockers.append("SELECTED_OPTION_HAS_NO_POSITIVE_LTP")
        if not isinstance(candidate, Mapping) or (_number(candidate.get("open_interest")) or 0) <= 0:
            blockers.append("SELECTED_OPTION_HAS_NO_REPORTED_OI")
        if isinstance(candidate, Mapping) and (_number(candidate.get("volume")) or 0) <= 0:
            warnings.append("SELECTED_OPTION_REPORTED_VOLUME_IS_ZERO")

    research_action = "NO_TRADE"
    if not blockers and direction == "LONG":
        research_action = "BUY_CE"
    elif not blockers and direction == "SHORT":
        research_action = "BUY_PE"

    execution_blockers = ["FNO_MARKET_BRAIN_V2_IS_SHADOW_ONLY", "PROSPECTIVE_VALIDATION_NOT_COMPLETE"]
    if not quality.get("execution_quote_complete"):
        execution_blockers.append("BID_ASK_EXECUTION_QUOTES_NOT_CAPTURED")

    return {
        "model_id": MODEL_ID,
        "perception_fingerprint": perception.get("perception_fingerprint"),
        "research_action": research_action,
        "research_candidate": candidate,
        "research_blockers": blockers,
        "warnings": warnings,
        "memory": dict(memory or {}),
        "memory_changed_direction": False,
        "memory_created_setup": False,
        "execution_action": "NO_TRADE",
        "execution_eligible": False,
        "execution_blockers": execution_blockers,
        "capital_committed": 0,
        "live_orders_created": False,
        "promotion_eligible": False,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_MARKET_BRAIN_V2_SHADOW_CONTRACT_V1",
        "layers": ["PERCEPTION", "EXPERIENCE_MEMORY", "DECISION_DISCIPLINE"],
        "perception_point_in_time_only": True,
        "future_snapshot_allowed": False,
        "memory_strictly_prior": True,
        "memory_outcome_availability_gated": True,
        "outcomes_used_for_similarity_ranking": False,
        "memory_can_create_setup": False,
        "memory_can_reverse_direction": False,
        "shadow_only": True,
        "live_execution": False,
        "capital_committed": 0,
        "allowed_research_actions": sorted(ALLOWED_RESEARCH_ACTIONS),
        "options_trade_generation_only": True,
        "futures_trade_generation": False,
    }
