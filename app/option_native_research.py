from __future__ import annotations

from collections import defaultdict

from .strategy_premium_replay import run_strategy_premium_replay

STRATEGIES = ("VWAP_TREND", "ORB_30", "BREAKOUT_20")


def _cost_adjusted_r(trade: dict, round_trip_cost_bps: float) -> float | None:
    r = trade.get("r_multiple")
    entry = trade.get("entry")
    sl = trade.get("stop_loss")
    if not isinstance(r, (int, float)):
        return None
    if not isinstance(entry, (int, float)) or not isinstance(sl, (int, float)):
        return float(r)
    risk = abs(float(entry) - float(sl))
    if risk <= 0:
        return float(r)
    cost = float(entry) * max(0.0, round_trip_cost_bps) / 10000.0
    return float(r) - cost / risk


def _summary(trades: list[dict], round_trip_cost_bps: float) -> dict:
    values = [x for x in (_cost_adjusted_r(t, round_trip_cost_bps) for t in trades) if x is not None]
    wins = sum(1 for x in values if x > 0)
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(values),
        "wins": wins,
        "losses": sum(1 for x in values if x < 0),
        "win_rate": round(wins / len(values) * 100, 1) if values else 0.0,
        "total_r": round(sum(values), 3),
        "average_r": round(sum(values) / len(values), 3) if values else 0.0,
        "max_drawdown_r": round(max_dd, 3),
    }


async def run_option_native_research(
    provider,
    symbols: list[str],
    start_date: str,
    end_date: str,
    research_target_r: float = 1.0,
    premium_min_rr: float = 1.5,
    max_trades_per_strategy: int = 50,
    round_trip_cost_bps: float = 10.0,
):
    """Research-only option-premium discovery.

    V3 deliberately does not alter Strategy Research v2 or the production scanner.
    It replays every frozen directional strategy into actual historical CE/PE premium
    candles, applies a configurable round-trip cost stress, and exposes diagnostics
    that can be used to discover where option buying does or does not preserve edge.
    """
    results = []
    all_errors = []
    for strategy in STRATEGIES:
        replay = await run_strategy_premium_replay(
            provider=provider,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            strategy=strategy,
            research_target_r=research_target_r,
            premium_min_rr=premium_min_rr,
            max_trades=max_trades_per_strategy,
        )
        trades = list(replay.get("trades") or [])
        buckets: dict[str, list[dict]] = defaultdict(list)
        for trade in trades:
            action = str(trade.get("action") or "UNKNOWN")
            buckets[action].append(trade)
        results.append({
            "strategy": strategy,
            "raw_summary": replay.get("summary") or {},
            "cost_adjusted_summary": _summary(trades, round_trip_cost_bps),
            "by_action": {name: _summary(rows, round_trip_cost_bps) for name, rows in sorted(buckets.items())},
            "candidate_signals_total": replay.get("candidate_signals_total", 0),
            "candidate_signals_selected": replay.get("candidate_signals_selected", 0),
            "trades": trades,
        })
        all_errors.extend({"strategy": strategy, **e} for e in (replay.get("errors") or []))

    leaderboard = sorted(
        results,
        key=lambda x: (
            float((x.get("cost_adjusted_summary") or {}).get("average_r", 0.0)),
            int((x.get("cost_adjusted_summary") or {}).get("trades", 0)),
        ),
        reverse=True,
    )
    for rank, row in enumerate(leaderboard, 1):
        row["rank"] = rank

    return {
        "mode": "ALPHAPILOT_OPTION_NATIVE_RESEARCH_V3",
        "research_only": True,
        "production_rules_changed": False,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": symbols,
        "research_target_r": research_target_r,
        "premium_min_risk_reward": premium_min_rr,
        "round_trip_cost_bps": round_trip_cost_bps,
        "leaderboard": leaderboard,
        "errors": all_errors,
        "limitations": [
            "Strategy Research v2 directional rules remain frozen and unchanged.",
            "V3 is research-only and cannot authorize a live AlphaPilot trade.",
            "LONG maps to BUY CE and SHORT maps to BUY PE using the existing historical contract-selection integrity rules.",
            "Cost adjustment is a configurable stress estimate; it is not a broker contract note reconstruction.",
            "A positive result must survive untouched symbol/time samples before promotion is considered.",
        ],
    }
