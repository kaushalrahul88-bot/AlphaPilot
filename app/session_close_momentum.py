from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta

from .backtest import IST, _historical, _ts
from .strategy_research import _atr, _day_indices


PROTOCOL_REVISION = "session-close-momentum-v1-2026-08-25"
SESSION_SYMBOLS = ("NIFTY", "BANKNIFTY")
VARIANTS = ("OPENING_SIGN", "PRE_CLOSE_SIGN", "OPENING_PRE_CLOSE_AGREEMENT")
DEVELOPMENT_START = datetime(2026, 4, 13).date()
DEVELOPMENT_END = datetime(2026, 7, 3).date()
ROUND_TRIP_COST_BPS = 2.0
STOP_ATR = 0.75
TARGET_R = 1.0


def _bar_at(rows: list[list], indices: list[int], at: time) -> int | None:
    return next((i for i in indices if (stamp := _ts(rows[i][0])) and stamp.time() == at), None)


def _direction(value: float) -> str | None:
    if value > 0:
        return "LONG"
    if value < 0:
        return "SHORT"
    return None


def _session_variants(rows: list[list], indices: list[int]) -> tuple[dict[str, str], dict] | None:
    open_index = _bar_at(rows, indices, time(9, 15))
    opening_end_index = _bar_at(rows, indices, time(9, 40))
    pre_close_index = _bar_at(rows, indices, time(14, 55))
    entry_index = _bar_at(rows, indices, time(15, 0))
    exit_index = _bar_at(rows, indices, time(15, 25))
    required = (open_index, opening_end_index, pre_close_index, entry_index, exit_index)
    if any(index is None for index in required):
        return None

    open_price = float(rows[open_index][1])
    opening_close = float(rows[opening_end_index][4])
    pre_close = float(rows[pre_close_index][4])
    if open_price <= 0:
        return None

    opening_return = opening_close / open_price - 1.0
    pre_close_return = pre_close / open_price - 1.0
    opening_direction = _direction(opening_return)
    pre_close_direction = _direction(pre_close_return)
    directions: dict[str, str] = {}
    if opening_direction:
        directions["OPENING_SIGN"] = opening_direction
    if pre_close_direction:
        directions["PRE_CLOSE_SIGN"] = pre_close_direction
    if opening_direction and opening_direction == pre_close_direction:
        directions["OPENING_PRE_CLOSE_AGREEMENT"] = opening_direction

    return directions, {
        "opening_return_bps": round(opening_return * 10_000.0, 2),
        "pre_close_return_bps": round(pre_close_return * 10_000.0, 2),
        "opening_direction": opening_direction,
        "pre_close_direction": pre_close_direction,
        "entry_index": entry_index,
        "exit_index": exit_index,
        "pre_close_index": pre_close_index,
    }


def _simulate_close_window(
    rows: list[list],
    indices: list[int],
    entry_index: int,
    exit_index: int,
    direction: str,
    atr: float,
) -> dict | None:
    if atr <= 0 or entry_index not in indices or exit_index not in indices or entry_index > exit_index:
        return None
    entry = float(rows[entry_index][1])
    risk = STOP_ATR * atr
    if entry <= 0 or risk <= 0:
        return None
    stop = entry - risk if direction == "LONG" else entry + risk
    target = entry + TARGET_R * risk if direction == "LONG" else entry - TARGET_R * risk
    cost_r = entry * ROUND_TRIP_COST_BPS / 10_000.0 / risk
    max_favourable = 0.0
    max_adverse = 0.0

    window = [i for i in indices if entry_index <= i <= exit_index]
    if not window:
        return None
    for i in window:
        high, low = float(rows[i][2]), float(rows[i][3])
        if direction == "LONG":
            max_favourable = max(max_favourable, (high - entry) / risk)
            max_adverse = max(max_adverse, (entry - low) / risk)
            hit_stop, hit_target = low <= stop, high >= target
        else:
            max_favourable = max(max_favourable, (entry - low) / risk)
            max_adverse = max(max_adverse, (high - entry) / risk)
            hit_stop, hit_target = high >= stop, low <= target
        if hit_stop and hit_target:
            return {
                "outcome": "AMBIGUOUS",
                "gross_r": None,
                "net_r": None,
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "mfe_r": round(max_favourable, 3),
                "mae_r": round(max_adverse, 3),
            }
        if hit_target:
            gross_r = TARGET_R
            outcome = "TARGET"
            break
        if hit_stop:
            gross_r = -1.0
            outcome = "SL"
            break
    else:
        exit_price = float(rows[exit_index][4])
        gross_r = (exit_price - entry) / risk if direction == "LONG" else (entry - exit_price) / risk
        outcome = "SESSION_CLOSE"

    return {
        "outcome": outcome,
        "gross_r": round(gross_r, 4),
        "net_r": round(gross_r - cost_r, 4),
        "cost_r": round(cost_r, 4),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "mfe_r": round(max_favourable, 3),
        "mae_r": round(max_adverse, 3),
    }


def _summary(trades: list[dict]) -> dict:
    resolved = [row for row in trades if isinstance(row.get("net_r"), (int, float))]
    values = [float(row["net_r"]) for row in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    average_r = sum(values) / len(values) if values else 0.0
    win_rate = len(wins) / len(values) * 100.0 if values else 0.0
    promising = len(values) >= 12 and average_r >= 0.10 and win_rate >= 55.0 and profit_factor >= 1.20
    return {
        "trades": len(values),
        "longs": sum(1 for row in resolved if row["direction"] == "LONG"),
        "shorts": sum(1 for row in resolved if row["direction"] == "SHORT"),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "average_r": round(average_r, 3),
        "total_r": round(sum(values), 3),
        "profit_factor": round(profit_factor, 2),
        "ambiguous": sum(1 for row in trades if row.get("outcome") == "AMBIGUOUS"),
        "state": "PROMISING" if promising else "INSUFFICIENT_OR_WEAK",
    }


async def run_session_close_momentum(provider, start_date: str, end_date: str) -> dict:
    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 12:
        raise ValueError("Session-Close Momentum blocks are limited to 12 calendar days")
    if start.date() < DEVELOPMENT_START or end.date() > DEVELOPMENT_END:
        raise ValueError("Session-Close Momentum v1 is frozen to 2026-04-13 through 2026-07-03 development dates")

    by_variant: dict[str, list[dict]] = defaultdict(list)
    errors: list[dict] = []
    sessions = 0
    for symbol in SESSION_SYMBOLS:
        try:
            rows = await _historical(provider, symbol, "5m", start - timedelta(days=5), end)
            rows = [row for row in rows if (stamp := _ts(row[0])) and stamp <= end]
            atrs = _atr(rows, 14)
            for day, indices in sorted(_day_indices(rows).items()):
                day_value = datetime.fromisoformat(day).date()
                if day_value < start.date() or day_value > end.date():
                    continue
                signal = _session_variants(rows, indices)
                if not signal:
                    continue
                directions, features = signal
                sessions += 1
                entry_index = int(features["entry_index"])
                exit_index = int(features["exit_index"])
                pre_close_index = int(features["pre_close_index"])
                atr = atrs[pre_close_index] if pre_close_index < len(atrs) else 0.0
                for variant, direction in directions.items():
                    simulation = _simulate_close_window(rows, indices, entry_index, exit_index, direction, atr)
                    if not simulation:
                        continue
                    entry_at = _ts(rows[entry_index][0])
                    exit_at = _ts(rows[exit_index][0])
                    by_variant[variant].append({
                        "variant": variant,
                        "symbol": symbol,
                        "session_date": day,
                        "direction": direction,
                        "action": "BUY CE" if direction == "LONG" else "BUY PE",
                        "entry_at": entry_at.isoformat() if entry_at else str(rows[entry_index][0]),
                        "scheduled_exit_at": exit_at.isoformat() if exit_at else str(rows[exit_index][0]),
                        "opening_return_bps": features["opening_return_bps"],
                        "pre_close_return_bps": features["pre_close_return_bps"],
                        "atr": round(atr, 4),
                        **simulation,
                    })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    summaries = [{"variant": variant, **_summary(by_variant[variant])} for variant in VARIANTS]
    return {
        "mode": "ALPHAPILOT_SESSION_CLOSE_MOMENTUM_V1",
        "protocol_revision": PROTOCOL_REVISION,
        "research_only": True,
        "production_rules_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": list(SESSION_SYMBOLS),
        "sessions": sessions,
        "observations": sum(row["trades"] for row in summaries),
        "summaries": summaries,
        "trades_by_variant": {variant: by_variant[variant] for variant in VARIANTS},
        "errors": errors,
        "fixed_protocol": {
            "opening_window": "09:15-09:45 IST",
            "pre_close_signal_cutoff": "15:00 IST",
            "entry": "15:00 IST open",
            "scheduled_exit": "15:25 IST close",
            "stop_atr": STOP_ATR,
            "target_r": TARGET_R,
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "min_block_trades": 12,
            "average_r": 0.10,
            "win_rate": 55.0,
            "profit_factor": 1.20,
            "replication_blocks_required": 4,
        },
        "source": {
            "paper": "Gao, Han, Li & Zhou (2018), Market intraday momentum",
            "journal": "Journal of Financial Economics 129(2), 394-414",
            "doi": "10.1016/j.jfineco.2018.05.009",
            "adaptation": "The published first-half-hour/final-half-hour timing idea is retained. ATR stop, fixed target, costs and NSE timestamps are AlphaPilot preregistered research rules.",
        },
        "limitations": [
            "This is underlying-index discovery only; it makes no claim about CE/PE premium profitability.",
            "The three variants were frozen together before any AlphaPilot result was observed.",
            "Only NIFTY and BANKNIFTY are tested because the source hypothesis concerns market-level intraday momentum.",
            "Same-bar stop/target collisions are ambiguous and excluded rather than guessed.",
            "A variant must pass unchanged in at least four of six blocks before true option-premium validation is allowed.",
            "No dates after 2026-07-03 are touched, preserving later data for a separately frozen validation if replication occurs.",
        ],
    }
