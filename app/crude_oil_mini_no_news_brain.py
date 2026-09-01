from __future__ import annotations

import hashlib
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

from .crude_oil_mini_data_probe import _complete_sessions

IST = ZoneInfo("Asia/Kolkata")
EXPECTED_CONTRACT = "CRUDEOILM21SEP26FUT"
CLICKS_PER_SESSION = 20
CLICK_START = time(10, 0)
CLICK_END = time(22, 0)
CLICK_SEED = "CRUDEOILM_NO_NEWS_V1_20_CLICKS"
BAR_MINUTES = 5
TARGET_R = 1.5
MEMORY_K = 50
MEMORY_MIN = 30
MEMORY_Z_MIN = 1.96
MEMORY_MEAN_ABS_MIN_PCT = 0.03
MIN_ALIGNED_LANES = 3
MAX_OPPOSING_LANES = 1


def _ts(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = float(value)
        if number > 1e12:
            number /= 1000.0
        parsed = datetime.fromtimestamp(number, IST)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_candles(candles):
    dedup = {}
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _ts(row[0])
            o, h, l, c = [float(row[i]) for i in range(1, 5)]
            volume = max(0.0, float(row[5] or 0.0)) if len(row) > 5 else 0.0
        except Exception:
            continue
        if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
            continue
        dedup[stamp] = [stamp.isoformat(), o, h, l, c, volume]
    return [dedup[key] for key in sorted(dedup)]


def bar_visible_at(row) -> datetime:
    return _ts(row[0]) + timedelta(minutes=BAR_MINUTES)


def visible_rows(candles, click_at):
    click = _ts(click_at)
    return [row for row in candles if bar_visible_at(row) <= click]


def _day_rows(candles, day):
    return [row for row in candles if _ts(row[0]).date() == day]


def deterministic_mini_clicks(candles, clicks_per_session=CLICKS_PER_SESSION):
    rows = clean_candles(candles)
    complete_days = {
        item["date"]
        for item in _complete_sessions(rows)
        if item.get("complete_for_20_click_research")
    }
    by_day = defaultdict(list)
    for row in rows:
        stamp = _ts(row[0])
        if stamp.date().isoformat() not in complete_days:
            continue
        # Clicks are wall-clock decisions. The bar starting exactly at the click
        # is deliberately invisible until five minutes later.
        if CLICK_START <= stamp.timetz().replace(tzinfo=None) <= CLICK_END:
            if len(visible_rows(_day_rows(rows, stamp.date()), stamp)) >= 12:
                by_day[stamp.date().isoformat()].append(stamp)

    clicks = []
    for day, candidates in sorted(by_day.items()):
        candidates = sorted(set(candidates))
        n = min(int(clicks_per_session), len(candidates))
        if n <= 0:
            continue
        seed = int(hashlib.sha256(f"{CLICK_SEED}:{day}".encode()).hexdigest()[:16], 16)
        chosen = sorted(random.Random(seed).sample(candidates, n))
        clicks.extend(
            {
                "session": day,
                "click_timestamp": stamp.isoformat(),
                "sampling": "DETERMINISTIC_TIMESTAMP_ONLY_10_TO_22_IST",
                "bar_visibility_policy": "BAR_START_PLUS_5_MINUTES",
            }
            for stamp in chosen
        )
    return clicks


def _rolling_return(closes, bars):
    if len(closes) <= bars:
        return None
    base = closes[-bars - 1]
    return (closes[-1] / base - 1.0) * 100.0 if base > 0 else None


def _atr_pct(rows, period=14):
    if len(rows) < 2:
        return None
    trs = []
    for i in range(1, len(rows)):
        high, low, previous = rows[i][2], rows[i][3], rows[i - 1][4]
        trs.append(max(high - low, abs(high - previous), abs(low - previous)))
    if not trs or rows[-1][4] <= 0:
        return None
    return mean(trs[-period:]) / rows[-1][4] * 100.0


def _structure(rows, window=24):
    sample = rows[-window:]
    if len(sample) < 12:
        return "UNKNOWN"
    half = len(sample) // 2
    older, recent = sample[:half], sample[half:]
    older_high, older_low = max(r[2] for r in older), min(r[3] for r in older)
    recent_high, recent_low = max(r[2] for r in recent), min(r[3] for r in recent)
    if recent_high > older_high and recent_low > older_low:
        return "UPTREND"
    if recent_high < older_high and recent_low < older_low:
        return "DOWNTREND"
    return "RANGE"


def _session_vwap(rows):
    total_volume = sum(max(0.0, r[5]) for r in rows)
    if total_volume <= 0:
        return mean(r[4] for r in rows) if rows else None
    return sum(((r[2] + r[3] + r[4]) / 3.0) * max(0.0, r[5]) for r in rows) / total_volume


def build_snapshot(candles, click_at):
    rows = clean_candles(candles)
    click = _ts(click_at)
    seen = visible_rows(rows, click)
    session = [row for row in seen if _ts(row[0]).date() == click.date()]
    if len(session) < 12:
        return None

    closes = [r[4] for r in session]
    last = session[-1]
    previous12 = session[-13:-1] if len(session) >= 13 else session[:-1]
    session_high = max(r[2] for r in session)
    session_low = min(r[3] for r in session)
    session_range = session_high - session_low
    vwap = _session_vwap(session)
    range_position = (last[4] - session_low) / session_range if session_range > 0 else 0.5
    vwap_gap = (last[4] / vwap - 1.0) * 100.0 if vwap and vwap > 0 else 0.0

    recent_volume = mean(r[5] for r in session[-3:])
    baseline_volume = mean(r[5] for r in session[-23:-3]) if len(session) >= 23 else mean(r[5] for r in session[:-3] or session)
    relative_volume = recent_volume / baseline_volume if baseline_volume > 0 else None

    breakout = "NONE"
    if previous12:
        prior_high = max(r[2] for r in previous12)
        prior_low = min(r[3] for r in previous12)
        if last[4] > prior_high:
            breakout = "ABOVE"
        elif last[4] < prior_low:
            breakout = "BELOW"

    latest_range = max(1e-9, last[2] - last[3])
    close_location = (last[4] - last[3]) / latest_range
    return {
        "click_timestamp": click.isoformat(),
        "latest_visible_bar_start": _ts(last[0]).isoformat(),
        "latest_visible_bar_at": bar_visible_at(last).isoformat(),
        "price": last[4],
        "session_open": session[0][1],
        "session_return_pct": (last[4] / session[0][1] - 1.0) * 100.0,
        "return_15m_pct": _rolling_return(closes, 3),
        "return_30m_pct": _rolling_return(closes, 6),
        "return_60m_pct": _rolling_return(closes, 12),
        "structure": _structure(session),
        "session_vwap": vwap,
        "session_vwap_gap_pct": vwap_gap,
        "session_range_position": range_position,
        "atr14_pct": _atr_pct(session),
        "relative_volume": relative_volume,
        "breakout": breakout,
        "latest_close_location": close_location,
        "visible_session_bars": len(session),
    }


def _feature_vector(features):
    values = [
        _f(features.get("session_return_pct"), 0.0),
        _f(features.get("return_15m_pct"), 0.0),
        _f(features.get("return_60m_pct"), 0.0),
        _f(features.get("session_vwap_gap_pct"), 0.0),
        _f(features.get("session_range_position"), 0.5),
        _f(features.get("atr14_pct"), 0.0),
        _f(features.get("relative_volume"), 1.0),
    ]
    return values


def build_memory_experiences(candles):
    rows = clean_candles(candles)
    by_day = defaultdict(list)
    for row in rows:
        by_day[_ts(row[0]).date()].append(row)
    experiences = []
    for day, day_rows in sorted(by_day.items()):
        day_rows = sorted(day_rows, key=lambda r: _ts(r[0]))
        for index in range(24, len(day_rows) - 6, 3):
            decision_time = _ts(day_rows[index][0]) + timedelta(minutes=5)
            snapshot = build_snapshot(rows, decision_time)
            if not snapshot:
                continue
            outcome_row = day_rows[index + 6]
            resolved_at = _ts(outcome_row[0]) + timedelta(minutes=5)
            future_return = (outcome_row[4] / snapshot["price"] - 1.0) * 100.0
            experiences.append({
                "decision_at": decision_time.isoformat(),
                "resolved_at": resolved_at.isoformat(),
                "vector": _feature_vector(snapshot),
                "future_30m_return_pct": future_return,
            })
    return experiences


def _memory_lane(experiences, features, click_at):
    click = _ts(click_at)
    safe = [e for e in experiences if _ts(e["resolved_at"]) <= click]
    if len(safe) < MEMORY_MIN:
        return {"lane": "MEMORY", "stance": "UNKNOWN", "resolved_pool": len(safe)}

    query = _feature_vector(features)
    columns = list(zip(*(e["vector"] for e in safe)))
    means = [mean(column) for column in columns]
    scales = [pstdev(column) or 1.0 for column in columns]

    ranked = []
    for experience in safe:
        distance = math.sqrt(sum(((x - m) / s - (q - m) / s) ** 2 for x, q, m, s in zip(experience["vector"], query, means, scales)))
        ranked.append((distance, experience))
    nearest = [item[1] for item in sorted(ranked, key=lambda item: item[0])[:MEMORY_K]]
    returns = [float(item["future_30m_return_pct"]) for item in nearest]
    if len(returns) < MEMORY_MIN:
        return {"lane": "MEMORY", "stance": "UNKNOWN", "resolved_pool": len(safe), "neighbors": len(returns)}
    avg = mean(returns)
    sd = pstdev(returns)
    se = sd / math.sqrt(len(returns)) if sd > 0 else 0.0
    z = avg / se if se > 0 else (999.0 if avg > 0 else -999.0 if avg < 0 else 0.0)
    stance = "UNKNOWN"
    if abs(avg) >= MEMORY_MEAN_ABS_MIN_PCT and abs(z) >= MEMORY_Z_MIN:
        stance = "BULLISH" if avg > 0 else "BEARISH"
    return {
        "lane": "MEMORY",
        "stance": stance,
        "resolved_pool": len(safe),
        "neighbors": len(returns),
        "mean_future_30m_return_pct": round(avg, 4),
        "z_score": round(z, 3),
    }


def _evidence_lanes(features, memory):
    ret15 = _f(features.get("return_15m_pct"), 0.0)
    ret30 = _f(features.get("return_30m_pct"), 0.0)
    ret60 = _f(features.get("return_60m_pct"), 0.0)
    structure = features.get("structure")
    vwap_gap = _f(features.get("session_vwap_gap_pct"), 0.0)
    position = _f(features.get("session_range_position"), 0.5)
    rel_volume = _f(features.get("relative_volume"))
    breakout = features.get("breakout")

    trend = "UNKNOWN"
    if structure == "UPTREND" and ret60 > 0:
        trend = "BULLISH"
    elif structure == "DOWNTREND" and ret60 < 0:
        trend = "BEARISH"

    momentum = "BULLISH" if ret15 > 0 and ret30 > 0 else "BEARISH" if ret15 < 0 and ret30 < 0 else "UNKNOWN"
    value = "BULLISH" if vwap_gap > 0 and position > 0.55 else "BEARISH" if vwap_gap < 0 and position < 0.45 else "UNKNOWN"
    participation = "UNKNOWN"
    if rel_volume is not None and rel_volume >= 1.10:
        participation = "BULLISH" if ret15 > 0 else "BEARISH" if ret15 < 0 else "UNKNOWN"
    break_lane = "BULLISH" if breakout == "ABOVE" else "BEARISH" if breakout == "BELOW" else "UNKNOWN"

    return [
        {"lane": "STRUCTURE", "stance": trend},
        {"lane": "MOMENTUM", "stance": momentum},
        {"lane": "VALUE_LOCATION", "stance": value},
        {"lane": "PARTICIPATION", "stance": participation},
        {"lane": "BREAKOUT", "stance": break_lane},
        memory,
    ]


def decide_no_news(features, memory):
    lanes = _evidence_lanes(features, memory)
    counts = Counter(item["stance"] for item in lanes)
    bull, bear = counts["BULLISH"], counts["BEARISH"]
    direction = None
    if bull >= MIN_ALIGNED_LANES and bear <= MAX_OPPOSING_LANES:
        direction = "BULLISH"
    elif bear >= MIN_ALIGNED_LANES and bull <= MAX_OPPOSING_LANES:
        direction = "BEARISH"

    # At least one direct price-confirmation lane must agree. Memory or volume
    # can strengthen a setup but can never create a trade by themselves.
    price_confirmation = any(
        item["stance"] == direction and item["lane"] in {"STRUCTURE", "MOMENTUM", "BREAKOUT"}
        for item in lanes
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
        "news_used": False,
    }


def _geometry(visible_session_rows, direction):
    if direction not in {"BULLISH", "BEARISH"} or len(visible_session_rows) < 6:
        return None
    recent3 = visible_session_rows[-3:]
    recent6 = visible_session_rows[-6:]
    if direction == "BULLISH":
        entry = max(r[2] for r in recent3)
        stop = min(r[3] for r in recent6)
        risk = entry - stop
        target = entry + TARGET_R * risk
    else:
        entry = min(r[3] for r in recent3)
        stop = max(r[2] for r in recent6)
        risk = stop - entry
        target = entry - TARGET_R * risk
    if risk <= 0:
        return None
    return {"entry": entry, "stop": stop, "target": target, "risk_points": risk, "target_r": TARGET_R}


def _resolve_geometry(day_rows, click_at, action, geometry):
    if action not in {"BUY_CE", "BUY_PE"} or not geometry:
        return None
    click = _ts(click_at)
    bullish = action == "BUY_CE"
    entry, stop, target, risk = geometry["entry"], geometry["stop"], geometry["target"], geometry["risk_points"]
    entered = False
    entry_at = None
    mfe = mae = 0.0
    for row in day_rows:
        stamp = _ts(row[0])
        if stamp < click:
            continue
        high, low = row[2], row[3]
        if not entered:
            if (bullish and high >= entry) or ((not bullish) and low <= entry):
                entered = True
                entry_at = stamp
            else:
                continue
        if bullish:
            mfe = max(mfe, (high - entry) / risk)
            mae = max(mae, (entry - low) / risk)
            hit_target, hit_stop = high >= target, low <= stop
        else:
            mfe = max(mfe, (entry - low) / risk)
            mae = max(mae, (high - entry) / risk)
            hit_target, hit_stop = low <= target, high >= stop
        if hit_target and hit_stop:
            return {"result": "STOP", "realized_r": -1.0, "same_bar_ambiguous": True, "entry_at": entry_at.isoformat(), "exit_at": stamp.isoformat(), "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}
        if hit_stop:
            return {"result": "STOP", "realized_r": -1.0, "entry_at": entry_at.isoformat(), "exit_at": stamp.isoformat(), "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}
        if hit_target:
            return {"result": "TARGET", "realized_r": TARGET_R, "entry_at": entry_at.isoformat(), "exit_at": stamp.isoformat(), "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}
    return {"result": "NO_ENTRY" if not entered else "SESSION_END", "realized_r": 0.0, "entry_at": entry_at.isoformat() if entry_at else None, "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}


def _horizon_return(rows, click_at, base_price, minutes):
    horizon = _ts(click_at) + timedelta(minutes=minutes)
    eligible = [row for row in rows if bar_visible_at(row) <= horizon]
    if not eligible or base_price <= 0:
        return None
    close = eligible[-1][4]
    return (close / base_price - 1.0) * 100.0


def _score(decisions, role):
    subset = [d for d in decisions if d["role"] == role]
    trades = [d for d in subset if d["action"] in {"BUY_CE", "BUY_PE"}]
    result = {
        "clicks": len(subset),
        "actions": dict(Counter(d["action"] for d in subset)),
        "trade_clicks": len(trades),
        "wait_clicks": sum(d["action"] == "WAIT" for d in subset),
    }
    for minutes in (15, 30, 60):
        signed = []
        aligned = 0
        for d in trades:
            raw = d["future_returns_pct"].get(str(minutes))
            if raw is None:
                continue
            value = raw if d["action"] == "BUY_CE" else -raw
            signed.append(value)
            aligned += value > 0
        result[f"horizon_{minutes}m"] = {
            "observations": len(signed),
            "direction_alignment_pct": round(aligned / len(signed) * 100.0, 2) if signed else None,
            "avg_signed_return_pct": round(mean(signed), 4) if signed else None,
        }
    resolved = [d["underlying_setup"] for d in trades if d.get("underlying_setup")]
    entered = [r for r in resolved if r.get("result") != "NO_ENTRY"]
    result["underlying_setup"] = {
        "resolved": len(resolved),
        "entered": len(entered),
        "outcomes": dict(Counter(r.get("result") for r in resolved)),
        "avg_realized_r_entered": round(mean(r.get("realized_r", 0.0) for r in entered), 4) if entered else None,
    }
    return result


def evaluate_crude_oil_mini_no_news(candles, contract=None):
    rows = clean_candles(candles)
    sessions = _complete_sessions(rows)
    complete_days = [item["date"] for item in sessions if item.get("complete_for_20_click_research")]
    clicks = deterministic_mini_clicks(rows)
    clicks_by_day = Counter(item["session"] for item in clicks)
    if any(clicks_by_day.get(day, 0) != CLICKS_PER_SESSION for day in complete_days):
        raise ValueError("Every complete Mini session must contribute exactly 20 deterministic clicks")

    split_index = max(1, math.ceil(len(complete_days) * 2.0 / 3.0))
    development_days = set(complete_days[:split_index])
    holdout_days = set(complete_days[split_index:])
    experiences = build_memory_experiences(rows)
    by_day = defaultdict(list)
    for row in rows:
        by_day[_ts(row[0]).date().isoformat()].append(row)

    decisions = []
    for click_meta in clicks:
        click = _ts(click_meta["click_timestamp"])
        snapshot = build_snapshot(rows, click)
        if not snapshot:
            continue
        memory = _memory_lane(experiences, snapshot, click)
        decision = decide_no_news(snapshot, memory)
        visible_session = visible_rows(by_day[click_meta["session"]], click)
        geometry = _geometry(visible_session, decision["direction"])
        setup = _resolve_geometry(by_day[click_meta["session"]], click, decision["action"], geometry)
        future_returns = {
            str(minutes): _horizon_return(by_day[click_meta["session"]], click, snapshot["price"], minutes)
            for minutes in (15, 30, 60)
        }
        decisions.append({
            **click_meta,
            "role": "DEVELOPMENT" if click_meta["session"] in development_days else "HOLDOUT",
            "action": decision["action"],
            "direction": decision["direction"],
            "confidence": decision["confidence"],
            "features": snapshot,
            "evidence": decision["evidence"],
            "geometry": geometry,
            "underlying_setup": setup,
            "future_returns_pct": future_returns,
            "option_premium_pnl": "UNSCORED_PROVIDER_HISTORY_UNAVAILABLE",
        })

    contract_info = contract or {}
    return {
        "mode": "CRUDE_OIL_MINI_NO_NEWS_CURRENT_MIND_V1",
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": "CRUDEOILM",
        "reference_contract": contract_info.get("trading_symbol") or EXPECTED_CONTRACT,
        "reference_contract_scope": "CURRENT_SEP_2026_MINI_FUTURE_ONLY_NOT_CONTINUOUS_FRONT_MONTH",
        "research_only": True,
        "news_enabled": False,
        "live_execution_enabled": False,
        "trade_expression": ["BUY_CE", "BUY_PE", "WAIT"],
        "historical_option_premium_scoring": False,
        "historical_option_premium_reason": "Groww backtesting API does not provide a validated MCX/COMMODITY Mini option history route; no synthetic premiums are used.",
        "bar_visibility_policy": "5m bar visible only at bar_start + 5 minutes",
        "click_schedule": {
            "clicks_per_complete_session": CLICKS_PER_SESSION,
            "window_ist": "10:00-22:00",
            "seed": CLICK_SEED,
            "outcome_blind": True,
        },
        "decision_policy": {
            "minimum_aligned_independent_lanes": MIN_ALIGNED_LANES,
            "maximum_opposing_lanes": MAX_OPPOSING_LANES,
            "price_confirmation_required": True,
            "target_r": TARGET_R,
            "thresholds_frozen_before_this_replay": True,
            "holdout_not_used_to_choose_thresholds": True,
        },
        "data": {
            "candles": len(rows),
            "sessions": len(sessions),
            "complete_sessions": len(complete_days),
            "complete_session_first": complete_days[0] if complete_days else None,
            "complete_session_last": complete_days[-1] if complete_days else None,
            "development_sessions": len(development_days),
            "holdout_sessions": len(holdout_days),
            "total_clicks": len(clicks),
            "memory_experiences": len(experiences),
        },
        "development": _score(decisions, "DEVELOPMENT"),
        "holdout": _score(decisions, "HOLDOUT"),
        "decisions": decisions,
        "integrity": {
            "regular_crude_used": False,
            "older_futures_contracts_used": False,
            "news_used": False,
            "synthetic_option_prices_used": False,
            "classification_decisions_created_before_outcomes_attached": True,
        },
    }
