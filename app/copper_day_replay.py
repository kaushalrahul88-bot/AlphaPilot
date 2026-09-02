from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time
from hashlib import sha256
from statistics import mean
from zoneinfo import ZoneInfo

from .commodities import analyze_commodity_candles
from .commodity_time import parse_ist_timestamp


IST = ZoneInfo("Asia/Kolkata")
DAILY_STARTING_CAPITAL = 10_000.0
DAILY_TARGET_PROFIT = 3_000.0
RISK_PER_TRADE_PCT = 5.0
RISK_PER_TRADE_RUPEES = DAILY_STARTING_CAPITAL * RISK_PER_TRADE_PCT / 100.0
CLICKS_PER_DAY = 10
CLICK_START_MINUTE = 10 * 60
CLICK_END_MINUTE = 22 * 60
CLICK_SALT = "alphapilot-copper-day-replay-v1"
SLIPPAGE_BPS_EACH_SIDE = 2.0
COST_BPS_EACH_SIDE = 2.0


def _f(value, default=0.0):
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def deterministic_click_times(day):
    """Ten reproducible result-independent 5m slots from 10:00 through 22:00 IST."""
    slots = range(CLICK_START_MINUTE, CLICK_END_MINUTE + 1, 5)
    ranked = sorted(
        slots,
        key=lambda minute: sha256(
            f"{CLICK_SALT}|{day.isoformat()}|{minute}".encode()
        ).digest(),
    )
    selected = sorted(ranked[:CLICKS_PER_DAY])
    return tuple(time(minute // 60, minute % 60) for minute in selected)


def _clean(rows):
    out = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = parse_ist_timestamp(row[0])
            opened, high, low, close = map(float, row[1:5])
            volume = float(row[5] or 0) if len(row) > 5 else 0.0
        except Exception:
            continue
        if min(opened, high, low, close) <= 0 or high < low:
            continue
        out.append([stamp, opened, high, low, close, max(0.0, volume)])
    out.sort(key=lambda row: row[0])
    return out


def _analysis_rows(rows):
    return [[r[0].isoformat(), r[1], r[2], r[3], r[4], r[5]] for r in rows]


def _trade_cost_r(entry, risk):
    if risk <= 0:
        return 0.0
    round_trip_fraction = 2.0 * (SLIPPAGE_BPS_EACH_SIDE + COST_BPS_EACH_SIDE) / 10000.0
    return entry * round_trip_fraction / risk


def _resolve_session_trade(plan, future_rows, entry_at):
    action = plan["action"]
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    target = float(plan["target1"])
    risk = entry - stop if action == "BUY" else stop - entry
    if risk <= 0:
        return {"outcome": "INVALID", "r_multiple": 0.0, "exit_at": None, "exit_price": entry}

    cost_r = _trade_cost_r(entry, risk)
    eligible = [row for row in future_rows if row[0] > entry_at]
    for row in eligible:
        stamp, _, high, low, _close, _volume = row
        if action == "BUY":
            hit_stop, hit_target = low <= stop, high >= target
        else:
            hit_stop, hit_target = high >= stop, low <= target

        # 5m OHLC cannot reveal intrabar order. Use stop-first for a conservative,
        # deterministic accounting rule rather than discarding the day.
        if hit_stop and hit_target:
            return {
                "outcome": "AMBIGUOUS_STOP_FIRST",
                "r_multiple": round(-1.0 - cost_r, 4),
                "exit_at": stamp.isoformat(),
                "exit_price": stop,
            }
        if hit_target:
            gross_r = abs(target - entry) / risk
            return {
                "outcome": "TARGET_HIT",
                "r_multiple": round(gross_r - cost_r, 4),
                "exit_at": stamp.isoformat(),
                "exit_price": target,
            }
        if hit_stop:
            return {
                "outcome": "STOP_HIT",
                "r_multiple": round(-1.0 - cost_r, 4),
                "exit_at": stamp.isoformat(),
                "exit_price": stop,
            }

    if eligible:
        stamp, _, _, _, final_close, _ = eligible[-1]
    else:
        stamp, final_close = entry_at, entry
    gross_r = (
        (final_close - entry) / risk
        if action == "BUY"
        else (entry - final_close) / risk
    )
    return {
        "outcome": "SESSION_CLOSE",
        "r_multiple": round(gross_r - cost_r, 4),
        "exit_at": stamp.isoformat(),
        "exit_price": round(final_close, 4),
    }


def _context_from_analysis(analysis):
    return {
        "alpha_score": analysis.get("alpha_score"),
        "market_structure": analysis.get("market_structure"),
        "rsi14": analysis.get("rsi14"),
        "roc10_pct": analysis.get("roc10_pct"),
        "ema9": analysis.get("ema9"),
        "ema20": analysis.get("ema20"),
        "ema50": analysis.get("ema50"),
        "atr14": analysis.get("atr14"),
        "recent_support": analysis.get("recent_support"),
        "recent_resistance": analysis.get("recent_resistance"),
        "bias_components": analysis.get("bias_components"),
    }


def _max_drawdown(pnl_path):
    equity = DAILY_STARTING_CAPITAL
    peak = equity
    worst = 0.0
    for pnl in pnl_path:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def replay_contract_rows(rows, contract_metadata=None):
    clean = _clean(rows)
    if not clean:
        raise RuntimeError("No usable stored Copper candles")

    timestamps = [row[0] for row in clean]
    by_day = defaultdict(list)
    for row in clean:
        by_day[row[0].date()].append(row)

    daily_results = []
    all_trades = []
    for day in sorted(by_day):
        session_rows = by_day[day]
        click_times = deterministic_click_times(day)
        decisions = []
        trades = []
        active_until = None
        pnl_path = []

        for click_clock in click_times:
            click_at = datetime.combine(day, click_clock, tzinfo=IST)
            if active_until is not None and click_at < active_until:
                decisions.append({
                    "clicked_at": click_at.isoformat(),
                    "decision": "POSITION_ACTIVE",
                    "reason": "Previous simulated trade had not exited yet.",
                })
                continue

            history_end = bisect_right(timestamps, click_at)
            history = clean[max(0, history_end - 260):history_end]
            session_stamps = [row[0] for row in session_rows]
            future_start = bisect_right(session_stamps, click_at)
            day_future = session_rows[future_start:]
            if len(history) < 60 or not day_future:
                decisions.append({
                    "clicked_at": click_at.isoformat(),
                    "decision": "WAIT",
                    "reason": "Insufficient completed history or no remaining session candles.",
                })
                continue

            analysis = analyze_commodity_candles("COPPER", _analysis_rows(history), min_rr=1.5)
            signal = str(analysis.get("signal") or "NO TRADE").upper()
            if signal not in {"BUY", "SELL"} or analysis.get("status") != "SETUP":
                decisions.append({
                    "clicked_at": click_at.isoformat(),
                    "decision": "WAIT",
                    "reason": analysis.get("reason") or "No directional opportunity at this moment.",
                    "context": _context_from_analysis(analysis),
                })
                continue

            plan = {
                "action": signal,
                "entry": float(analysis["entry"]),
                "stop": float(analysis["stop_loss"]),
                "target1": float(analysis["target1"]),
                "target2": float(analysis["target2"]),
            }
            outcome = _resolve_session_trade(plan, day_future, click_at)
            pnl = round(outcome["r_multiple"] * RISK_PER_TRADE_RUPEES, 2)
            trade = {
                "date": day.isoformat(),
                "clicked_at": click_at.isoformat(),
                "action": signal,
                "confidence": analysis.get("alpha_score"),
                "entry": plan["entry"],
                "stop": plan["stop"],
                "target1": plan["target1"],
                "target2": plan["target2"],
                "instrument": "MCX COPPER FUTURE",
                "setup_side": signal,
                "option_contract": None,
                "option_side": None,
                "option_entry": None,
                "option_stop_loss": None,
                "option_target": None,
                "option_data_available": False,
                "option_note": "This replay contains Copper futures OHLC only; no historical MCX option premium/chain was used.",
                "planned_risk_rupees": RISK_PER_TRADE_RUPEES,
                **outcome,
                "net_pnl_rupees": pnl,
                "context": _context_from_analysis(analysis),
            }
            trades.append(trade)
            all_trades.append(trade)
            pnl_path.append(pnl)
            exit_at = outcome.get("exit_at")
            active_until = parse_ist_timestamp(exit_at) if exit_at else None
            decisions.append({
                "clicked_at": click_at.isoformat(),
                "decision": signal,
                "trade_number": len(trades),
                "confidence": analysis.get("alpha_score"),
                "entry": plan["entry"],
                "stop": plan["stop"],
                "target1": plan["target1"],
                "outcome": outcome["outcome"],
                "net_pnl_rupees": pnl,
            })

        net_pnl = round(sum(t["net_pnl_rupees"] for t in trades), 2)
        ending_capital = round(DAILY_STARTING_CAPITAL + net_pnl, 2)
        wins = sum(t["net_pnl_rupees"] > 0 for t in trades)
        losses = sum(t["net_pnl_rupees"] < 0 for t in trades)
        daily_results.append({
            "date": day.isoformat(),
            "starting_capital_rupees": DAILY_STARTING_CAPITAL,
            "click_times_ist": [t.strftime("%H:%M") for t in click_times],
            "decisions": len(decisions),
            "trades": len(trades),
            "buy_trades": sum(t["action"] == "BUY" for t in trades),
            "sell_trades": sum(t["action"] == "SELL" for t in trades),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / len(trades) * 100.0, 2) if trades else 0.0,
            "net_pnl_rupees": net_pnl,
            "ending_capital_rupees": ending_capital,
            "return_on_daily_capital_pct": round(net_pnl / DAILY_STARTING_CAPITAL * 100.0, 2),
            "max_intraday_drawdown_rupees": round(_max_drawdown(pnl_path), 2),
            "target_profit_rupees": DAILY_TARGET_PROFIT,
            "target_3000_achieved": net_pnl >= DAILY_TARGET_PROFIT,
            "best_trade_pnl_rupees": max((t["net_pnl_rupees"] for t in trades), default=0.0),
            "worst_trade_pnl_rupees": min((t["net_pnl_rupees"] for t in trades), default=0.0),
            "trade_details": trades,
            "decision_timeline": decisions,
        })

    monthly = []
    month_groups = defaultdict(list)
    for day in daily_results:
        month_groups[day["date"][:7]].append(day)
    for month in sorted(month_groups):
        days = month_groups[month]
        trades = [trade for day in days for trade in day["trade_details"]]
        net = round(sum(day["net_pnl_rupees"] for day in days), 2)
        target_hits = sum(day["target_3000_achieved"] for day in days)
        positive_days = sum(day["net_pnl_rupees"] > 0 for day in days)
        negative_days = sum(day["net_pnl_rupees"] < 0 for day in days)
        wins = sum(t["net_pnl_rupees"] > 0 for t in trades)
        monthly.append({
            "month": month,
            "trading_days": len(days),
            "independent_daily_starting_capital_rupees": DAILY_STARTING_CAPITAL,
            "daily_capital_resets": True,
            "sum_of_daily_net_pnl_rupees": net,
            "average_daily_pnl_rupees": round(net / len(days), 2) if days else 0.0,
            "positive_days": positive_days,
            "negative_days": negative_days,
            "flat_days": len(days) - positive_days - negative_days,
            "profitable_day_rate_pct": round(positive_days / len(days) * 100.0, 2) if days else 0.0,
            "target_3000_days": target_hits,
            "target_3000_hit_rate_pct": round(target_hits / len(days) * 100.0, 2) if days else 0.0,
            "trades": len(trades),
            "wins": wins,
            "losses": sum(t["net_pnl_rupees"] < 0 for t in trades),
            "win_rate_pct": round(wins / len(trades) * 100.0, 2) if trades else 0.0,
            "best_day_pnl_rupees": max(day["net_pnl_rupees"] for day in days),
            "worst_day_pnl_rupees": min(day["net_pnl_rupees"] for day in days),
            "max_daily_drawdown_rupees": max(day["max_intraday_drawdown_rupees"] for day in days),
        })

    total_net = round(sum(day["net_pnl_rupees"] for day in daily_results), 2)
    total_trades = len(all_trades)
    total_wins = sum(t["net_pnl_rupees"] > 0 for t in all_trades)
    target_days = sum(day["target_3000_achieved"] for day in daily_results)
    positive_days = sum(day["net_pnl_rupees"] > 0 for day in daily_results)
    return {
        "mode": "COPPER_DAY_BY_DAY_CAPITAL_REPLAY_V1",
        "research_only": True,
        "production_rules_changed": False,
        "symbol": "COPPER",
        "contract": contract_metadata or {},
        "coverage": {
            "first_candle_at": clean[0][0].isoformat(),
            "last_candle_at": clean[-1][0].isoformat(),
            "candles": len(clean),
            "trading_days": len(daily_results),
        },
        "capital_model": {
            "starting_capital_each_day_rupees": DAILY_STARTING_CAPITAL,
            "capital_resets_each_day": True,
            "daily_target_profit_rupees": DAILY_TARGET_PROFIT,
            "risk_per_trade_pct_of_daily_start": RISK_PER_TRADE_PCT,
            "planned_risk_per_trade_rupees": RISK_PER_TRADE_RUPEES,
            "broker_margin_feasibility_modelled": False,
            "note": "₹10,000 is treated as a research bankroll. Full-lot MCX Copper margin feasibility is not implied.",
        },
        "decision_model": {
            "clicks_per_day": CLICKS_PER_DAY,
            "click_window_ist": "10:00-22:00",
            "click_selection": "deterministic pseudo-random 5m slots; independent of future results",
            "one_open_position_at_a_time": True,
            "brain": "existing analyze_commodity_candles opportunity score",
            "market_context_is_permission_gate": False,
            "target_rr": 1.5,
            "cost_bps_each_side": COST_BPS_EACH_SIDE,
            "slippage_bps_each_side": SLIPPAGE_BPS_EACH_SIDE,
            "ambiguous_5m_bar_rule": "stop-first",
            "session_close_rule": "close any unresolved position at final available session candle",
            "lookahead": False,
        },
        "overall_summary": {
            "trading_days": len(daily_results),
            "positive_days": positive_days,
            "negative_days": sum(day["net_pnl_rupees"] < 0 for day in daily_results),
            "flat_days": sum(day["net_pnl_rupees"] == 0 for day in daily_results),
            "profitable_day_rate_pct": round(positive_days / len(daily_results) * 100.0, 2) if daily_results else 0.0,
            "target_3000_days": target_days,
            "target_3000_hit_rate_pct": round(target_days / len(daily_results) * 100.0, 2) if daily_results else 0.0,
            "sum_of_independent_daily_pnl_rupees": total_net,
            "average_daily_pnl_rupees": round(total_net / len(daily_results), 2) if daily_results else 0.0,
            "trades": total_trades,
            "wins": total_wins,
            "losses": sum(t["net_pnl_rupees"] < 0 for t in all_trades),
            "win_rate_pct": round(total_wins / total_trades * 100.0, 2) if total_trades else 0.0,
            "best_day_pnl_rupees": max((day["net_pnl_rupees"] for day in daily_results), default=0.0),
            "worst_day_pnl_rupees": min((day["net_pnl_rupees"] for day in daily_results), default=0.0),
        },
        "audit_scope": {
            "trade_setup_fields_included": [
                "clicked_at", "instrument", "setup_side", "entry", "stop",
                "target1", "target2", "outcome", "exit_at", "exit_price",
                "r_multiple", "net_pnl_rupees", "context"
            ],
            "option_replay_status": "NOT_AVAILABLE_IN_THIS_DATASET",
            "option_replay_reason": "Stored replay rows are Copper futures candles. No historical option contract/premium series is present in this replay.",
        },
        "monthly_summary": monthly,
        "daily_results": daily_results,
    }


async def run_copper_day_by_day_replay_from_store(store, days=3650):
    end = datetime.now(IST)
    start = datetime(2020, 1, 1, tzinfo=IST)
    await store.initialize()
    segments = await store.read_symbol_contract_segments("COPPER", 5, start, end)
    if not segments:
        raise RuntimeError("No stored Copper 5m history available")
    # Current available history is intentionally replayed exactly as stored.
    # If future rollovers add more segments, each segment can be replayed without
    # crossing its own candle sequence.
    combined_daily = []
    segment_reports = []
    for segment in segments:
        candles = segment.get("candles") or []
        if not candles:
            continue
        report = replay_contract_rows(candles, {
            "trading_symbol": segment.get("trading_symbol"),
            "expiry_date": segment.get("expiry_date"),
        })
        segment_reports.append(report)

    if len(segment_reports) == 1:
        return segment_reports[0]

    # For future multi-contract storage, return separate contract replays rather
    # than fabricating continuity across rollovers.
    return {
        "mode": "COPPER_DAY_BY_DAY_CAPITAL_REPLAY_MULTI_SEGMENT_V1",
        "research_only": True,
        "production_rules_changed": False,
        "segments": segment_reports,
        "note": "Each contract is replayed independently; no trade crosses a futures rollover.",
    }
