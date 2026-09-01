from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from statistics import mean

from .crude_news_intelligence import apply_crude_news_intelligence
from .crude_oil_mini_historical_news_v1 import (
    crude_oil_mini_historical_news_metadata_v1,
    crude_oil_mini_historical_news_v1,
)
from .crude_oil_mini_no_news_brain import (
    MAX_OPPOSING_LANES,
    MIN_ALIGNED_LANES,
    _geometry,
    _horizon_return,
    _resolve_geometry,
    _score,
    _ts,
    clean_candles,
    evaluate_crude_oil_mini_no_news,
    visible_rows,
)

_PRICE_CONFIRMATION_LANES = {"STRUCTURE", "MOMENTUM", "BREAKOUT"}


def _session_boundaries(rows):
    by_day = defaultdict(list)
    for row in rows:
        by_day[_ts(row[0]).date().isoformat()].append(row)
    ordered_days = sorted(by_day)
    result = {}
    previous_end = None
    for day in ordered_days:
        day_rows = sorted(by_day[day], key=lambda row: _ts(row[0]))
        result[day] = {
            "start": _ts(day_rows[0][0]),
            "end": _ts(day_rows[-1][0]),
            "previous_market_bar": previous_end,
        }
        previous_end = _ts(day_rows[-1][0])
    return by_day, result


def _news_lane(click_timestamp, session, enriched_records):
    click = _ts(click_timestamp)
    start = session["start"]
    previous_market_bar = session.get("previous_market_bar")
    active = []
    for row in enriched_records:
        intelligence = row.get("news_intelligence") or {}
        if intelligence.get("disposition") != "ALLOW":
            continue
        available = _ts(row.get("available_at"))
        if available > click:
            continue
        in_current_session = available >= start
        carried_from_closed_market = (
            available < start
            and (previous_market_bar is None or available > previous_market_bar)
        )
        if not (in_current_session or carried_from_closed_market):
            continue
        active.append(row)

    bullish_weight = sum(
        float((row.get("news_intelligence") or {}).get("confidence") or 0.0)
        for row in active
        if (row.get("news_intelligence") or {}).get("effect") == "BULLISH"
    )
    bearish_weight = sum(
        float((row.get("news_intelligence") or {}).get("confidence") or 0.0)
        for row in active
        if (row.get("news_intelligence") or {}).get("effect") == "BEARISH"
    )
    if bullish_weight > bearish_weight:
        stance = "BULLISH"
    elif bearish_weight > bullish_weight:
        stance = "BEARISH"
    else:
        stance = "UNKNOWN"
    return {
        "lane": "NEWS",
        "stance": stance,
        "bullish_weight": round(bullish_weight, 4),
        "bearish_weight": round(bearish_weight, 4),
        "active_record_count": len(active),
        "active_records": [
            {
                "event_id": row.get("event_id"),
                "underlying_event_id": row.get("underlying_event_id"),
                "available_at": row.get("available_at"),
                "source": row.get("source"),
                "headline": (row.get("value") or {}).get("headline"),
                "effect": (row.get("news_intelligence") or {}).get("effect"),
                "confidence": (row.get("news_intelligence") or {}).get("confidence"),
                "event_type": (row.get("news_intelligence") or {}).get("event_type"),
            }
            for row in active
        ],
        "activation_policy": "CURRENT_SESSION_OR_CLOSED_MARKET_CARRY_TO_NEXT_SESSION",
    }


def _decide_with_news(baseline_evidence, news_lane):
    lanes = [deepcopy(item) for item in baseline_evidence] + [deepcopy(news_lane)]
    counts = Counter(str(item.get("stance") or "UNKNOWN") for item in lanes)
    bull, bear = counts["BULLISH"], counts["BEARISH"]
    direction = None
    if bull >= MIN_ALIGNED_LANES and bear <= MAX_OPPOSING_LANES:
        direction = "BULLISH"
    elif bear >= MIN_ALIGNED_LANES and bull <= MAX_OPPOSING_LANES:
        direction = "BEARISH"

    price_confirmation = any(
        item.get("stance") == direction and item.get("lane") in _PRICE_CONFIRMATION_LANES
        for item in baseline_evidence
    ) if direction else False
    if not price_confirmation:
        direction = None

    action = "BUY_CE" if direction == "BULLISH" else "BUY_PE" if direction == "BEARISH" else "WAIT"
    aligned = bull if direction == "BULLISH" else bear if direction == "BEARISH" else max(bull, bear)
    confidence = min(85, 50 + max(0, aligned - MIN_ALIGNED_LANES + 1) * 8) if direction else min(55, 35 + max(bull, bear) * 5)
    return {
        "action": action,
        "direction": direction or "NEUTRAL",
        "confidence": confidence,
        "evidence": lanes,
        "bullish_lanes": bull,
        "bearish_lanes": bear,
        "news_used": news_lane.get("stance") in {"BULLISH", "BEARISH"},
    }


def _score_all(decisions):
    tagged = [{**row, "role": "ALL"} for row in decisions]
    return _score(tagged, "ALL")


def _metric_delta(news_score, baseline_score):
    result = {
        "trade_clicks": int(news_score.get("trade_clicks") or 0) - int(baseline_score.get("trade_clicks") or 0),
        "wait_clicks": int(news_score.get("wait_clicks") or 0) - int(baseline_score.get("wait_clicks") or 0),
    }
    for minutes in (15, 30, 60):
        key = f"horizon_{minutes}m"
        before = baseline_score.get(key) or {}
        after = news_score.get(key) or {}
        b = before.get("avg_signed_return_pct")
        a = after.get("avg_signed_return_pct")
        result[key] = {
            "direction_alignment_pp": (
                round(float(after.get("direction_alignment_pct")) - float(before.get("direction_alignment_pct")), 4)
                if after.get("direction_alignment_pct") is not None and before.get("direction_alignment_pct") is not None
                else None
            ),
            "avg_signed_return_pct": round(float(a) - float(b), 4) if a is not None and b is not None else None,
        }
    before_setup = baseline_score.get("underlying_setup") or {}
    after_setup = news_score.get("underlying_setup") or {}
    b = before_setup.get("avg_realized_r_entered")
    a = after_setup.get("avg_realized_r_entered")
    result["underlying_setup"] = {
        "entered": int(after_setup.get("entered") or 0) - int(before_setup.get("entered") or 0),
        "avg_realized_r_entered": round(float(a) - float(b), 4) if a is not None and b is not None else None,
    }
    return result


def evaluate_crude_oil_mini_news_comparison(candles, contract=None, news_records=None, news_metadata=None):
    rows = clean_candles(candles)
    baseline = evaluate_crude_oil_mini_no_news(rows, contract)
    baseline_decisions = list(baseline.get("decisions") or [])
    frozen_clicks = [row["click_timestamp"] for row in baseline_decisions]

    raw_news = list(news_records if news_records is not None else crude_oil_mini_historical_news_v1())
    intelligence = apply_crude_news_intelligence(raw_news)
    enriched = list(intelligence.get("records") or [])
    metadata = dict(news_metadata if news_metadata is not None else crude_oil_mini_historical_news_metadata_v1())
    metadata.update({
        "news_intelligence_mode": intelligence.get("mode"),
        "news_intelligence_counts": intelligence.get("counts"),
        "news_intelligence_policy": intelligence.get("policy"),
    })

    by_day, boundaries = _session_boundaries(rows)
    news_decisions = []
    action_changes = Counter()
    for baseline_row in baseline_decisions:
        click = baseline_row["click_timestamp"]
        session_key = baseline_row["session"]
        session = boundaries[session_key]
        lane = _news_lane(click, session, enriched)

        # Freeze the news-aware action using only baseline point-in-time evidence plus news
        # available by the click. Baseline outcome/future return is intentionally not read here.
        decision = _decide_with_news(baseline_row.get("evidence") or [], lane)
        visible_session = visible_rows(by_day[session_key], click)
        geometry = _geometry(visible_session, decision["direction"])

        # Only after the news action and geometry are frozen do we reveal the future.
        setup = _resolve_geometry(by_day[session_key], click, decision["action"], geometry)
        price = float((baseline_row.get("features") or {}).get("price") or 0.0)
        future_returns = {
            str(minutes): _horizon_return(by_day[session_key], click, price, minutes)
            for minutes in (15, 30, 60)
        }
        baseline_action = baseline_row.get("action")
        if baseline_action != decision["action"]:
            action_changes[f"{baseline_action}->{decision['action']}"] += 1
        news_decisions.append({
            "session": session_key,
            "click_timestamp": click,
            "sampling": baseline_row.get("sampling"),
            "bar_visibility_policy": baseline_row.get("bar_visibility_policy"),
            "role": baseline_row.get("role"),
            "baseline_action": baseline_action,
            "action": decision["action"],
            "direction": decision["direction"],
            "confidence": decision["confidence"],
            "features": deepcopy(baseline_row.get("features") or {}),
            "baseline_evidence": deepcopy(baseline_row.get("evidence") or []),
            "news_evidence": lane,
            "evidence": decision["evidence"],
            "geometry": geometry,
            "underlying_setup": setup,
            "future_returns_pct": future_returns,
            "baseline_underlying_setup": deepcopy(baseline_row.get("underlying_setup")),
            "option_premium_pnl": "UNSCORED_PROVIDER_HISTORY_UNAVAILABLE",
        })

    news_clicks = [row["click_timestamp"] for row in news_decisions]
    click_identity_exact = frozen_clicks == news_clicks
    if not click_identity_exact:
        raise RuntimeError("News replay did not preserve the frozen no-news click sequence")

    baseline_all = _score_all(baseline_decisions)
    news_all = _score_all(news_decisions)
    changed = [row for row in news_decisions if row.get("baseline_action") != row.get("action")]
    changed_baseline_outcomes = Counter(
        str((row.get("baseline_underlying_setup") or {}).get("result") or "NONE")
        for row in changed
    )
    changed_news_outcomes = Counter(
        str((row.get("underlying_setup") or {}).get("result") or "NONE")
        for row in changed
    )

    return {
        "mode": "CRUDE_OIL_MINI_NO_NEWS_VS_PIT_NEWS_V1",
        "product": "CRUDE_OIL_MINI",
        "reference_contract": baseline.get("reference_contract"),
        "reference_contract_scope": baseline.get("reference_contract_scope"),
        "research_only": True,
        "live_execution_enabled": False,
        "historical_option_premium_scoring": False,
        "comparison_design": {
            "same_current_expiry_contract": True,
            "same_candles": True,
            "same_clicks": True,
            "same_baseline_market_brain": True,
            "only_added_input": "POINT_IN_TIME_CRUDE_NEWS_INTELLIGENCE",
            "baseline_frozen_before_news_replay": True,
            "future_outcomes_read_for_news_decision": False,
        },
        "data": deepcopy(baseline.get("data") or {}),
        "news_metadata": metadata,
        "click_identity_exact": click_identity_exact,
        "baseline": {
            "all": baseline_all,
            "development": baseline.get("development"),
            "holdout": baseline.get("holdout"),
        },
        "with_news": {
            "all": news_all,
            "development": _score(news_decisions, "DEVELOPMENT"),
            "holdout": _score(news_decisions, "HOLDOUT"),
        },
        "delta": {
            "all": _metric_delta(news_all, baseline_all),
            "development": _metric_delta(_score(news_decisions, "DEVELOPMENT"), baseline.get("development") or {}),
            "holdout": _metric_delta(_score(news_decisions, "HOLDOUT"), baseline.get("holdout") or {}),
        },
        "changed_clicks": len(changed),
        "action_change_counts": dict(sorted(action_changes.items())),
        "changed_baseline_outcomes": dict(sorted(changed_baseline_outcomes.items())),
        "changed_news_outcomes": dict(sorted(changed_news_outcomes.items())),
        "changed_rows": changed,
        "decisions": news_decisions,
        "guardrails": [
            "The current-expiry CRUDEOILM no-news replay is computed first and its click sequence is frozen.",
            "The news replay uses the exact same current-expiry Mini candles and exact same click timestamps.",
            "Only Crude News Intelligence ALLOW records can contribute a directional NEWS lane.",
            "News published after a click is invisible to that click.",
            "Closed-market news may carry into the next market session; stale news from older sessions is not revived.",
            "News cannot create a trade without at least one confirming direct price lane from structure, momentum, or breakout.",
            "Future candles and trade outcomes are revealed only after the news-aware action is frozen.",
            "No Copper dates, Copper thresholds, Copper news records, option premiums, IV, Greeks, or option P&L are used.",
        ],
    }
