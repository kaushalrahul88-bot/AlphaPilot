from __future__ import annotations


MODE = "COPPER_DIRECTION_BRAIN_V2_SHADOW"
DIRECTIONAL = {"BULLISH", "BEARISH"}
INDEPENDENT_FAMILIES = (
    "LOCAL_STRUCTURE",
    "OPTION_PARTICIPATION",
    "GLOBAL_COPPER",
    "CHINA_DEMAND",
    "EVENT_REACTION",
    "EXPERIENCE_MEMORY",
)


def _number(value):
    try:
        if value is None:
            return None
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError, OverflowError):
        return None


def _direction_from_number(value) -> str:
    value = _number(value)
    if value is None or value == 0:
        return "UNKNOWN"
    return "BULLISH" if value > 0 else "BEARISH"


def _family(
    name: str,
    *,
    origin: str,
    stance: str = "UNKNOWN",
    counts: bool = False,
    state: str,
    detail=None,
) -> dict:
    normalized = str(stance or "UNKNOWN").upper()
    if normalized not in DIRECTIONAL:
        normalized = "UNKNOWN"
        counts = False
    return {
        "family": name,
        "causal_origin": origin,
        "stance": normalized,
        "counts_for_direction": bool(counts and normalized in DIRECTIONAL),
        "state": state,
        "detail": detail or {},
    }


def local_structure_family(board: dict) -> dict:
    market = (((board.get("groups") or {}).get("primary_market") or {}).get("MCX_COPPER") or {})
    snapshot = market.get("perception_snapshot") or {}
    if market.get("status") != "AVAILABLE" or not snapshot:
        return _family(
            "LOCAL_STRUCTURE",
            origin="LOCAL_PRICE_STRUCTURE",
            state="PIT_MARKET_PERCEPTION_NOT_READY",
            detail={
                "market_status": market.get("status"),
                "perception_status": market.get("perception_status"),
                "reason": market.get("perception_reason") or market.get("reason"),
            },
        )

    structure = str(snapshot.get("structure") or "UNKNOWN").upper()
    structure_stance = (
        "BULLISH" if structure == "UPTREND"
        else "BEARISH" if structure == "DOWNTREND"
        else "UNKNOWN"
    )
    momentum_15 = _direction_from_number(snapshot.get("return_15m_pct"))
    momentum_60 = _direction_from_number(snapshot.get("return_60m_pct"))
    momentum = [stance for stance in (momentum_15, momentum_60) if stance in DIRECTIONAL]

    if structure_stance in DIRECTIONAL:
        opposing = [stance for stance in momentum if stance != structure_stance]
        supporting = [stance for stance in momentum if stance == structure_stance]
        if opposing:
            stance = "UNKNOWN"
            state = "INTERNAL_LOCAL_CONTRADICTION"
        elif supporting:
            stance = structure_stance
            state = "STRUCTURE_CONFIRMED_BY_MOMENTUM"
        else:
            stance = structure_stance
            state = "STRUCTURE_ONLY"
    elif len(momentum) == 2 and len(set(momentum)) == 1:
        stance = momentum[0]
        state = "MOMENTUM_COHERENT_WITHOUT_STRUCTURE"
    else:
        stance = "UNKNOWN"
        state = "NO_COHERENT_LOCAL_DIRECTION"

    return _family(
        "LOCAL_STRUCTURE",
        origin="LOCAL_PRICE_STRUCTURE",
        stance=stance,
        counts=stance in DIRECTIONAL,
        state=state,
        detail={
            "structure": structure,
            "return_15m_pct": _number(snapshot.get("return_15m_pct")),
            "return_60m_pct": _number(snapshot.get("return_60m_pct")),
            "momentum_15m": momentum_15,
            "momentum_60m": momentum_60,
            "session_vwap_gap_pct": _number(snapshot.get("session_vwap_gap_pct")),
            "opening_range_break": snapshot.get("opening_range_break"),
            "price_oi_state": snapshot.get("price_oi_state"),
        },
    )


def option_participation_family(board: dict) -> dict:
    option = (((board.get("groups") or {}).get("option_market") or {}).get("MCX_COPPER_OPTION") or {})
    if option.get("status") != "AVAILABLE":
        state = "NO_FIRST_SEEN_OPTION_SNAPSHOT"
    else:
        state = "RAW_OPTION_POSITIONING_CONTEXT_ONLY"
    return _family(
        "OPTION_PARTICIPATION",
        origin="OPTION_MARKET_POSITIONING",
        state=state,
        detail={
            "status": option.get("status"),
            "sample_bucket_at": option.get("sample_bucket_at"),
            "put_call_oi_ratio": option.get("put_call_oi_ratio"),
            "ce_open_interest": option.get("ce_open_interest"),
            "pe_open_interest": option.get("pe_open_interest"),
            "first_seen_immutable": option.get("first_seen_immutable"),
            "rule": "Raw option OI or one snapshot cannot vote; a separate preregistered OI-plus-premium change model is required.",
        },
    )


def global_copper_family(board: dict) -> dict:
    global_group = ((board.get("groups") or {}).get("global_copper") or {})
    comex = global_group.get("COMEX_HG") or {}
    lme = global_group.get("LME_COPPER") or {}
    return _family(
        "GLOBAL_COPPER",
        origin="GLOBAL_COPPER_PRICE_DISCOVERY",
        state="LICENSED_FIRST_SEEN_GLOBAL_TAPE_NOT_CONNECTED",
        detail={
            "comex_status": comex.get("status"),
            "comex_reason": comex.get("reason"),
            "lme_status": lme.get("status"),
            "lme_reason": lme.get("reason"),
            "rule": "Public delayed/current quotes and end-of-day values cannot create an intraday Global Copper vote.",
        },
    )


def china_demand_family(board: dict) -> dict:
    macro = (((board.get("groups") or {}).get("china_macro") or {}).get("MACRO_RELEASE") or {})
    return _family(
        "CHINA_DEMAND",
        origin="CHINA_PHYSICAL_AND_MACRO_DEMAND",
        state="SLOW_MACRO_CONTEXT_ONLY" if macro.get("status") == "AVAILABLE" else "MACRO_UNAVAILABLE",
        detail={
            "status": macro.get("status"),
            "available_at": macro.get("available_at"),
            "records": macro.get("records") or [],
            "rule": "Absolute macro levels such as PMI above/below 50 cannot create an intraday direction vote without a preregistered event-surprise and price-reaction model.",
        },
    )


def event_reaction_family(board: dict) -> dict:
    news = (((board.get("groups") or {}).get("news") or {}).get("COPPER_NEWS") or {})
    return _family(
        "EVENT_REACTION",
        origin="COPPER_EVENT_CAUSAL_REACTION",
        state=(
            "NO_PROSPECTIVE_FIRST_DETECTED_NEWS"
            if news.get("status") != "AVAILABLE"
            else "NEWS_PRESENT_BUT_NO_REGISTERED_PRICE_REACTION_CONFIRMATION"
        ),
        detail={
            "news_status": news.get("status"),
            "news_reason": news.get("reason"),
            "rule": "Headline sentiment never votes. Mechanism, materiality, novelty and a PIT-confirmed price reaction are required.",
        },
    )


def experience_memory_family(board: dict) -> dict:
    memory = (((board.get("groups") or {}).get("experience_memory") or {}).get("DIRECTION_MEMORY") or {})
    if memory.get("status") == "AVAILABLE" and memory.get("registered_directional_stance") in DIRECTIONAL:
        # Reserved for a future separately registered prospective memory contract.
        stance = str(memory["registered_directional_stance"]).upper()
        registered = bool(memory.get("direction_vote_registered"))
        return _family(
            "EXPERIENCE_MEMORY",
            origin="HISTORICAL_ANALOGUE",
            stance=stance if registered else "UNKNOWN",
            counts=registered,
            state="REGISTERED_PROSPECTIVE_MEMORY" if registered else "MEMORY_NOT_REGISTERED_FOR_DIRECTION",
            detail=memory,
        )
    return _family(
        "EXPERIENCE_MEMORY",
        origin="HISTORICAL_ANALOGUE",
        state="NO_REGISTERED_PROSPECTIVE_DIRECTION_MEMORY_CONNECTED",
        detail={
            "rule": "Outcome memory may vote only after its analogue selection and outcome-availability rules are separately frozen and validated.",
        },
    )


def context_modifiers(board: dict) -> dict:
    currency = ((board.get("groups") or {}).get("currency") or {})
    positioning = ((board.get("groups") or {}).get("positioning") or {})
    return {
        "FX_TRANSLATION": {
            "role": "MODIFIER_ONLY",
            "counts_for_direction": False,
            "intraday": currency.get("USDINR_INTRADAY") or {},
            "slow_reference": currency.get("SLOW_REFERENCE_FX") or {},
            "rule": "Daily reference FX cannot independently create or reverse Copper direction.",
        },
        "CFTC_POSITIONING": {
            "role": "SLOW_CONTEXT_ONLY",
            "counts_for_direction": False,
            "data": positioning.get("CFTC_COPPER") or {},
            "rule": "Weekly CFTC positioning is regime/context information, not an intraday trigger.",
        },
    }


def _thesis(families: list[dict]) -> dict:
    counted = [
        row for row in families
        if row.get("counts_for_direction") and row.get("stance") in DIRECTIONAL
    ]
    by_origin = {}
    duplicate_origins = []
    for row in counted:
        origin = str(row.get("causal_origin") or row.get("family") or "UNKNOWN")
        if origin in by_origin:
            duplicate_origins.append(origin)
            continue
        by_origin[origin] = row
    independent = list(by_origin.values())
    bullish = [row for row in independent if row.get("stance") == "BULLISH"]
    bearish = [row for row in independent if row.get("stance") == "BEARISH"]

    if bullish and bearish:
        return {
            "direction": "UNKNOWN",
            "confidence": "CONFLICTED",
            "state": "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION",
            "supporting_families": [],
            "opposing_families": sorted(row["family"] for row in independent),
            "counted_families": [row["family"] for row in independent],
            "duplicate_causal_origins_suppressed": sorted(set(duplicate_origins)),
        }

    supporting = bullish or bearish
    if len(supporting) < 2:
        return {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
            "supporting_families": sorted(row["family"] for row in supporting),
            "opposing_families": [],
            "counted_families": [row["family"] for row in independent],
            "duplicate_causal_origins_suppressed": sorted(set(duplicate_origins)),
        }

    return {
        "direction": "BULLISH" if bullish else "BEARISH",
        "confidence": "STRONG" if len(supporting) >= 3 else "MODERATE",
        "state": "COHERENT_DIRECTION_THESIS",
        "supporting_families": sorted(row["family"] for row in supporting),
        "opposing_families": [],
        "counted_families": [row["family"] for row in independent],
        "duplicate_causal_origins_suppressed": sorted(set(duplicate_origins)),
    }


def evaluate_copper_direction_v2_shadow(board: dict) -> dict:
    families = [
        local_structure_family(board),
        option_participation_family(board),
        global_copper_family(board),
        china_demand_family(board),
        event_reaction_family(board),
        experience_memory_family(board),
    ]
    thesis = _thesis(families)
    return {
        "mode": MODE,
        "product": "COPPER",
        "trade_instrument": "OPTIONS_ONLY",
        "as_of": board.get("as_of"),
        "research_only": True,
        "shadow_only": True,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "counted_families": thesis["counted_families"],
        "duplicate_causal_origins_suppressed": thesis["duplicate_causal_origins_suppressed"],
        "families": {row["family"]: row for row in families},
        "modifiers": context_modifiers(board),
        "current_mind_action": None,
        "setup_geometry_generated": False,
        "option_expression_generated": False,
        "sealed_current_mind_effect": "NONE",
        "decision_effect": "NONE",
        "option_expression_effect": "NONE",
        "production_rules_changed": False,
        "historical_backfill_used": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "promotion_eligible": False,
        "rules": [
            "No weighted indicator score is used.",
            "At least two independent causal origins must align and no counted origin may oppose them.",
            "Local structure and local momentum belong to one LOCAL_STRUCTURE causal family.",
            "Raw option OI and one option snapshot cannot vote direction.",
            "Price-plus-volume alone is not an independent Participation direction family.",
            "China macro levels are slow context until an event-surprise plus PIT price-reaction rule is separately registered.",
            "COMEX/LME require timestamp-safe first-seen global data before Global Copper may vote.",
            "Headline sentiment cannot vote; event direction requires mechanism, materiality, novelty and confirmed PIT reaction.",
            "Daily FX and weekly CFTC positioning are modifiers/context only.",
            "Experience Memory may vote only under a separately registered, outcome-availability-safe prospective memory contract.",
            "Direction does not imply entry readiness or an option trade.",
        ],
        "integration_contract": integration_contract(),
    }


def integration_contract() -> dict:
    return {
        "version": "COPPER_DIRECTION_BRAIN_V2_SHADOW_V1",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_expression_effect": "NONE",
        "production_rules_changed": False,
        "independent_direction_families": list(INDEPENDENT_FAMILIES),
        "causal_origin_deduplication": True,
        "minimum_independent_confirmations": 2,
        "raw_option_oi_directional_vote_allowed": False,
        "legacy_price_volume_participation_vote_allowed": False,
        "absolute_macro_level_directional_vote_allowed": False,
        "headline_sentiment_direction_allowed": False,
        "slow_fx_directional_vote_allowed": False,
        "weekly_cftc_directional_vote_allowed": False,
        "public_delayed_comex_substitution_allowed": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "promotion_allowed": False,
    }
