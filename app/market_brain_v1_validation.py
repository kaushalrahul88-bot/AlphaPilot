from __future__ import annotations

from typing import Any


CORE_DIMENSIONS = (
    "point_in_time_integrity",
    "direction_reading",
    "abstention_quality",
    "experience_memory",
    "incremental_value",
    "execution_separation",
)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _check_mapping(report: dict) -> tuple[str, dict]:
    checks = _dict(report.get("checks"))
    if not checks:
        return "INCOMPLETE", {}
    normalized = {str(key): bool(value) for key, value in checks.items()}
    return ("VERIFIED" if all(normalized.values()) else "FAILED"), normalized


def _direction_summary(direction_audit: dict) -> dict:
    brains = _dict(direction_audit.get("brains"))
    result: dict[str, Any] = {"status": "MISSING_REQUIRED_EVIDENCE", "horizons": {}}
    observed = 0
    for horizon in ("30", "60", "120"):
        a = _dict(_dict(brains.get("A")).get(horizon))
        b = _dict(_dict(brains.get("B")).get(horizon))
        a_obs = int(a.get("observations") or 0)
        b_obs = int(b.get("observations") or 0)
        observed += a_obs + b_obs
        a_acc = _number(a.get("direction_accuracy_pct"))
        b_acc = _number(b.get("direction_accuracy_pct"))
        a_signed = _number(a.get("avg_signed_forward_pct"))
        b_signed = _number(b.get("avg_signed_forward_pct"))
        result["horizons"][horizon] = {
            "brain_a": {
                "observations": a_obs,
                "direction_accuracy_pct": a_acc,
                "avg_signed_forward_pct": a_signed,
            },
            "brain_b": {
                "observations": b_obs,
                "direction_accuracy_pct": b_acc,
                "avg_signed_forward_pct": b_signed,
            },
            "brain_b_minus_a": {
                "direction_accuracy_pct_points": (
                    round(b_acc - a_acc, 4) if a_acc is not None and b_acc is not None else None
                ),
                "avg_signed_forward_pct": (
                    round(b_signed - a_signed, 6)
                    if a_signed is not None and b_signed is not None
                    else None
                ),
            },
        }
    if direction_audit.get("mode") == "COPPER_MARKET_BRAIN_DIRECTION_AUDIT_V1" and observed > 0:
        result["status"] = "MEASURED"
    result["interpretation"] = (
        "Brain A vs Brain B is a frozen simple-vs-richer comparison. Differences are descriptive; "
        "they are not a promotion rule or a tuning target."
    )
    return result


def _abstention_summary(abstention_audit: dict | None) -> dict:
    report = _dict(abstention_audit)
    if not report:
        return {
            "status": "MISSING_REQUIRED_EVIDENCE",
            "reason": (
                "Existing directional audits skip NO_TRADE observations, so they cannot tell us whether "
                "AlphaPilot correctly abstained or missed a large move."
            ),
            "required_metrics": [
                "no_trade_observations",
                "no_trade_followed_by_large_move",
                "no_trade_opportunity_cost",
                "missed_move_gap_attribution",
                "wait_conversion_quality",
            ],
        }
    return {
        "status": "MEASURED",
        "mode": report.get("mode"),
        "no_trade_observations": report.get("no_trade_observations"),
        "no_trade_followed_by_large_move": report.get("no_trade_followed_by_large_move"),
        "large_move_threshold_pct": report.get("large_move_threshold_pct"),
        "gap_counts": report.get("gap_counts"),
    }


def _memory_summary(memory_audit: dict) -> dict:
    experiences = int(memory_audit.get("experiences") or 0)
    queries = int(memory_audit.get("walk_forward_queries") or 0)
    valid = (
        memory_audit.get("mode") == "COPPER_EXPERIENCE_MEMORY_V1"
        and experiences > 0
        and queries > 0
        and memory_audit.get("production_rules_changed") is False
    )
    return {
        "status": "MEASURED" if valid else "MISSING_REQUIRED_EVIDENCE",
        "experiences": experiences,
        "walk_forward_queries": queries,
        "analogue_k": memory_audit.get("analogue_k"),
        "production_rules_changed": memory_audit.get("production_rules_changed"),
        "rule": "Memory must remain walk-forward and may not retrieve future experiences.",
    }


def _incremental_value_summary(context_ablation: dict | None) -> dict:
    report = _dict(context_ablation)
    if not report:
        return {
            "status": "MISSING_REQUIRED_EVIDENCE",
            "reason": "No frozen ablation report supplied.",
            "required_comparisons": [
                "simple_price_structure_baseline",
                "structure_plus_participation",
                "structure_plus_context",
                "full_candidate_brain",
            ],
        }
    return {
        "status": "MEASURED",
        "mode": report.get("mode"),
        "available_fields": sorted(report.keys()),
        "rule": "A component earns production influence only through pre-registered out-of-sample incremental value.",
    }


def _news_summary(news_reaction_audit: dict | None) -> dict:
    report = _dict(news_reaction_audit)
    if not report:
        return {"status": "NOT_SUPPLIED", "required_for_core_readiness": False}
    classified = int(report.get("classified") or 0)
    return {
        "status": "SHADOW_MEASURED" if classified else "INSUFFICIENT_EVIDENCE",
        "classified_events": classified,
        "events": int(report.get("events") or 0),
        "observed_path_counts": report.get("observed_path_counts"),
        "materiality_qualified_path_counts": report.get("materiality_qualified_path_counts"),
        "required_for_core_readiness": False,
        "rule": "News remains hypothesis/context until the market response is independently observed and validated.",
    }


def build_market_brain_v1_validation(
    *,
    data_integrity: dict | None = None,
    direction_audit: dict | None = None,
    error_attribution: dict | None = None,
    experience_memory: dict | None = None,
    context_ablation: dict | None = None,
    abstention_audit: dict | None = None,
    news_reaction_audit: dict | None = None,
) -> dict:
    """Consolidate existing research evidence without changing Market Brain behavior.

    The scorecard intentionally distinguishes *measurement coverage* from performance. It can say
    that a dimension has been measured, is missing, or violates an invariant; it cannot promote a
    strategy because a descriptive metric happens to look attractive.
    """
    data = _dict(data_integrity)
    direction = _dict(direction_audit)
    memory = _dict(experience_memory)
    errors = _dict(error_attribution)

    data_status, data_checks = _check_mapping(data)
    pit_status = data_status
    if direction and direction.get("same_session_only") is not True:
        pit_status = "FAILED"
    point_in_time = {
        "status": pit_status,
        "data_checks": data_checks,
        "same_session_forward_outcomes": direction.get("same_session_only"),
        "reference_contract": data.get("reference_contract") or direction.get("reference_contract"),
    }

    execution_checks = {
        "trade_instrument_options": direction.get("trade_instrument") == "OPTIONS",
        "underlying_reference_only": direction.get("underlying_reference_role") == "REFERENCE_ONLY",
        "futures_pnl_not_used": direction.get("futures_pnl_calculated") is False,
        "synthetic_option_premium_not_used": direction.get("synthetic_option_premium_used") is False,
    }
    execution_status = (
        "VERIFIED"
        if direction and all(execution_checks.values())
        else ("FAILED" if direction else "MISSING_REQUIRED_EVIDENCE")
    )

    dimensions = {
        "point_in_time_integrity": point_in_time,
        "direction_reading": _direction_summary(direction),
        "abstention_quality": _abstention_summary(abstention_audit),
        "experience_memory": _memory_summary(memory),
        "incremental_value": _incremental_value_summary(context_ablation),
        "execution_separation": {"status": execution_status, "checks": execution_checks},
    }

    blockers = [
        name
        for name in CORE_DIMENSIONS
        if _dict(dimensions.get(name)).get("status")
        in {"FAILED", "INCOMPLETE", "MISSING_REQUIRED_EVIDENCE"}
    ]

    return {
        "mode": "MARKET_BRAIN_V1_CONSOLIDATION_SCORECARD",
        "research_only": True,
        "descriptive_only": True,
        "production_rules_changed": False,
        "strategy_rules_changed": False,
        "promotion_status": "NOT_READY" if blockers else "VALIDATION_EVIDENCE_COMPLETE_NOT_PROMOTED",
        "blockers": blockers,
        "dimensions": dimensions,
        "error_attribution": {
            "status": "MEASURED"
            if errors.get("mode") == "COPPER_MARKET_BRAIN_ERROR_ATTRIBUTION_V1"
            else "NOT_SUPPLIED",
            "descriptive_only": errors.get("descriptive_only"),
            "strategy_rules_changed": errors.get("strategy_rules_changed"),
        },
        "news_reaction_shadow": _news_summary(news_reaction_audit),
        "next_required_work": (
            "Build a frozen point-in-time NO_TRADE/WAIT abstention audit before adding more intelligence layers."
            if "abstention_quality" in blockers
            else "Run pre-registered out-of-sample validation before considering production promotion."
        ),
        "guardrails": [
            "Freeze the candidate brain during consolidation; do not tune rules from this scorecard.",
            "Measurement coverage is not the same as predictive edge.",
            "Directional accuracy alone cannot establish option profitability.",
            "NO_TRADE followed by a large move must be audited, not silently counted as a safe abstention.",
            "News/reaction intelligence remains shadow evidence until larger-sample validation is complete.",
            "Underlying thesis, trade decision, and option expression remain separate stages.",
        ],
    }
