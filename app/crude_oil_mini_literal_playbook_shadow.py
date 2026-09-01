from __future__ import annotations

from collections import Counter
from statistics import mean

ACTIONABLE = {"BUY_CE", "BUY_PE"}
SUPPORTED_PLAYBOOKS = {"TREND_PULLBACK", "BREAKOUT_RETEST", "RANGE_EDGE_REVERSAL"}


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _aligned(action: str, bullish: bool) -> bool:
    return (action == "BUY_CE" and bullish) or (action == "BUY_PE" and not bullish)


def literal_playbook_confirmation(row: dict) -> dict:
    """Verify the declared Crude playbook from information visible at the click.

    This is deliberately a shadow diagnostic. It does not alter the Current Mind
    action. Confirmation rules express the literal chart meaning of each playbook
    using only contemporaneous Crude features and causal prior-session profiles.
    No threshold is searched against outcomes.
    """
    action = str(row.get("action") or "WAIT")
    decision = row.get("decision") or {}
    playbook = decision.get("playbook")
    features = row.get("features") or {}
    profile = row.get("profile") or {}

    if action not in ACTIONABLE or not playbook:
        return {
            "status": "NOT_APPLICABLE",
            "declared_playbook": playbook,
            "confirmed": False,
            "checks": {},
        }
    if playbook not in SUPPORTED_PLAYBOOKS:
        return {
            "status": "UNSUPPORTED_PLAYBOOK",
            "declared_playbook": playbook,
            "confirmed": False,
            "checks": {},
        }

    checks: dict[str, bool] = {}
    scale_source = None

    if playbook == "TREND_PULLBACK":
        structure = str(features.get("structure") or "UNKNOWN")
        atr_pct = _f(features.get("atr_pct"))
        ema20_gap = _f(features.get("ema20_gap_pct"))
        ret15 = _f(features.get("return_15m_pct"))
        ret60 = _f(features.get("return_60m_pct"))

        checks["trend_direction_matches_action"] = (
            (action == "BUY_CE" and structure == "UPTREND")
            or (action == "BUY_PE" and structure == "DOWNTREND")
        )
        checks["ema20_reaccepted_in_trade_direction"] = bool(
            ema20_gap is not None
            and ((action == "BUY_CE" and ema20_gap >= 0) or (action == "BUY_PE" and ema20_gap <= 0))
        )
        checks["within_one_atr_of_ema20"] = bool(
            atr_pct is not None and atr_pct > 0 and ema20_gap is not None and abs(ema20_gap) <= atr_pct
        )
        checks["short_momentum_realigns"] = bool(
            ret15 is not None and ((action == "BUY_CE" and ret15 > 0) or (action == "BUY_PE" and ret15 < 0))
        )
        checks["one_hour_context_supports_trend"] = bool(
            ret60 is not None and ((action == "BUY_CE" and ret60 > 0) or (action == "BUY_PE" and ret60 < 0))
        )
        scale_source = "CONTEMPORANEOUS_ATR"

    elif playbook == "BREAKOUT_RETEST":
        opening_break = str(features.get("opening_range_break") or "UNKNOWN")
        price = _f(features.get("price"))
        atr_points = _f(features.get("atr_points"))
        ret15 = _f(features.get("return_15m_pct"))
        boundary = _f(
            features.get("opening_range_high") if action == "BUY_CE" else features.get("opening_range_low")
        )

        checks["breakout_direction_matches_action"] = (
            (action == "BUY_CE" and opening_break == "ABOVE")
            or (action == "BUY_PE" and opening_break == "BELOW")
        )
        checks["price_still_accepted_beyond_boundary"] = bool(
            price is not None
            and boundary is not None
            and ((action == "BUY_CE" and price >= boundary) or (action == "BUY_PE" and price <= boundary))
        )
        checks["retest_is_within_one_atr_of_boundary"] = bool(
            price is not None
            and boundary is not None
            and atr_points is not None
            and atr_points > 0
            and abs(price - boundary) <= atr_points
        )
        checks["short_momentum_realigns"] = bool(
            ret15 is not None and ((action == "BUY_CE" and ret15 > 0) or (action == "BUY_PE" and ret15 < 0))
        )
        scale_source = "CONTEMPORANEOUS_ATR"

    elif playbook == "RANGE_EDGE_REVERSAL":
        structure = str(features.get("structure") or "UNKNOWN")
        position = _f(features.get("session_range_position"))
        low_edge = _f(profile.get("range_position_low"))
        high_edge = _f(profile.get("range_position_high"))
        ret15 = _f(features.get("return_15m_pct"))

        checks["structure_is_range"] = structure == "RANGE"
        checks["at_causal_range_edge"] = bool(
            position is not None
            and low_edge is not None
            and high_edge is not None
            and ((action == "BUY_CE" and position <= low_edge) or (action == "BUY_PE" and position >= high_edge))
        )
        checks["rejection_momentum_matches_reversal"] = bool(
            ret15 is not None and ((action == "BUY_CE" and ret15 > 0) or (action == "BUY_PE" and ret15 < 0))
        )
        scale_source = "PRIOR_COMPLETE_SESSION_RANGE_QUANTILES"

    confirmed = bool(checks) and all(checks.values())
    return {
        "status": "CONFIRMED" if confirmed else "NOT_CONFIRMED",
        "declared_playbook": playbook,
        "confirmed": confirmed,
        "checks": checks,
        "failed_checks": sorted(key for key, value in checks.items() if not value),
        "scale_source": scale_source,
        "point_in_time": True,
        "outcomes_used_for_confirmation": False,
    }


def _signed_forward_60(row: dict):
    raw = (row.get("future_returns_pct") or {}).get("60")
    if raw is None:
        return None
    value = float(raw)
    return value if row.get("action") == "BUY_CE" else -value


def _resolved_r(row: dict):
    outcome = row.get("outcome") or {}
    if outcome.get("result") not in {"TARGET", "STOP"}:
        return None
    return _f(outcome.get("realized_r"))


def _metrics(rows: list[dict]) -> dict:
    signed = [value for value in (_signed_forward_60(row) for row in rows) if value is not None]
    resolved = [value for value in (_resolved_r(row) for row in rows) if value is not None]
    return {
        "observations": len(rows),
        "direction_accuracy_60m_pct": round(sum(value > 0 for value in signed) / len(signed) * 100.0, 2) if signed else None,
        "avg_signed_forward_60m_pct": round(mean(signed), 4) if signed else None,
        "resolved_setups": len(resolved),
        "avg_realized_r_resolved": round(mean(resolved), 4) if resolved else None,
        "targets": sum((row.get("outcome") or {}).get("result") == "TARGET" for row in rows),
        "stops": sum((row.get("outcome") or {}).get("result") == "STOP" for row in rows),
    }


def _three_session_windows(session_dates: list[str]) -> list[set[str]]:
    dates = list(dict.fromkeys(sorted(session_dates)))
    if not dates:
        return [set(), set(), set()]
    base, remainder = divmod(len(dates), 3)
    sizes = [base + (1 if i < remainder else 0) for i in range(3)]
    out = []
    cursor = 0
    for size in sizes:
        out.append(set(dates[cursor: cursor + size]))
        cursor += size
    return out


def evaluate_literal_playbook_shadow(baseline: dict) -> dict:
    decisions = list(baseline.get("decisions") or [])
    actionable = [row for row in decisions if row.get("action") in ACTIONABLE]
    session_dates = list(baseline.get("complete_session_dates") or sorted({row.get("session") for row in decisions if row.get("session")}))
    windows = _three_session_windows(session_dates)

    annotated = []
    for row in actionable:
        confirmation = literal_playbook_confirmation(row)
        annotated.append({
            "session": row.get("session"),
            "click_timestamp": row.get("click_timestamp"),
            "action": row.get("action"),
            "declared_playbook": confirmation.get("declared_playbook"),
            "confirmation": confirmation,
            "future_returns_pct": row.get("future_returns_pct"),
            "outcome": row.get("outcome"),
            "decision_fingerprint": row.get("decision_fingerprint"),
        })

    playbooks = {}
    for playbook in sorted(SUPPORTED_PLAYBOOKS):
        declared_pairs = [(raw, ann) for raw, ann in zip(actionable, annotated) if ann.get("declared_playbook") == playbook]
        declared = [raw for raw, _ in declared_pairs]
        confirmed = [raw for raw, ann in declared_pairs if (ann.get("confirmation") or {}).get("confirmed")]
        failure_counter = Counter()
        for _, ann in declared_pairs:
            for failed in (ann.get("confirmation") or {}).get("failed_checks", []):
                failure_counter[failed] += 1

        window_rows = []
        for index, window in enumerate(windows, start=1):
            window_confirmed = [row for row in confirmed if row.get("session") in window]
            window_rows.append({
                "window": index,
                "sessions": len(window),
                "first_session": min(window) if window else None,
                "last_session": max(window) if window else None,
                "confirmed": _metrics(window_confirmed),
            })

        playbooks[playbook] = {
            "declared": len(declared),
            "confirmed": len(confirmed),
            "confirmation_rate_pct": round(len(confirmed) / len(declared) * 100.0, 2) if declared else None,
            "declared_metrics": _metrics(declared),
            "confirmed_metrics": _metrics(confirmed),
            "failed_checks": dict(failure_counter.most_common()),
            "chronological_windows": window_rows,
        }

    confirmed_rows = [
        raw for raw, ann in zip(actionable, annotated)
        if (ann.get("confirmation") or {}).get("confirmed")
    ]
    return {
        "mode": "CRUDE_OIL_MINI_LITERAL_PLAYBOOK_CONFIRMATION_SHADOW_V1",
        "reference_contract": baseline.get("reference_contract"),
        "baseline_clicks": baseline.get("evaluated_clicks"),
        "baseline_actionable_setups": len(actionable),
        "literal_confirmed_setups": len(confirmed_rows),
        "candidate_if_confirmation_were_required": _metrics(confirmed_rows),
        "playbooks": playbooks,
        "annotations": annotated,
        "strategy_rules_changed": False,
        "decision_effect": "SHADOW_ONLY",
        "news_used": False,
        "option_market_data_used": False,
        "outcomes_used_to_define_confirmation_rules": False,
        "guardrails": [
            "The frozen Current Mind action and decision fingerprint are never modified by this module.",
            "Literal confirmation uses only features available at the click and causal prior-session profiles.",
            "One contemporaneous ATR is a structural distance scale, not an outcome-fitted Crude threshold.",
            "Range edges reuse prior-complete-session quantiles already produced by the causal perception profile.",
            "Performance is descriptive; a promising confirmed subset is a hypothesis, not an automatic production gate.",
        ],
    }
