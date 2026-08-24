from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .backtest import IST, _historical, _ts
from .execution_structure_research import LOOKAHEAD_BARS, PULLBACK_ATR
from .fno_historical_backtest import _available_contracts, _instrument_rows, _select_expiry
from .fno_premium_replay import replay_option_trade
from .setup_discovery_v2 import _failed_breakout_reversal
from .strategy_research import _atr, _day_indices

ROUND_TRIP_COST_BPS = 10.0
OPTION_TYPE = "CE"
SETUP_TYPE = "FAILED_BREAKOUT_REVERSAL"
DIRECTION = "LONG"
METHOD = "PULLBACK_ENTRY"
TARGET_R = 1.0

# Frozen option-OOS gates. These are research gates only and cannot modify production.
MIN_FILLED_SIGNALS = 20
MIN_OPTION_COVERAGE = 50.0
MIN_WIN_RATE = 55.0
MIN_AVG_R = 0.10
MIN_PF = 1.20


def _candidate_h_fill(rows, signal_i: int, stop: float, atr: float):
    anchor = float(rows[signal_i][4])
    limit = anchor - PULLBACK_ATR * atr
    for j in range(signal_i + 1, min(len(rows), signal_i + 1 + LOOKAHEAD_BARS)):
        low = float(rows[j][3])
        if low > limit:
            continue
        if low <= stop:
            return {"status": "AMBIGUOUS_PREENTRY", "fill_i": j, "underlying_entry": limit}
        when = _ts(rows[j][0])
        return {
            "status": "FILLED",
            "fill_i": j,
            "fill_at": when.isoformat() if when else str(rows[j][0]),
            "underlying_entry": limit,
            "underlying_stop": stop,
        }
    return {"status": "UNFILLED"}


def _profit_factor(rs: list[float]) -> float:
    wins = sum(x for x in rs if x > 0)
    losses = abs(sum(x for x in rs if x < 0))
    return wins / losses if losses > 0 else (99.0 if wins > 0 else 0.0)


def _net_r_from_replay(replay: dict) -> float | None:
    scenario = (replay.get("target_scenarios") or {}).get("1.0R") or {}
    raw_r = scenario.get("r_multiple")
    entry = replay.get("option_entry")
    risk_pct = replay.get("premium_risk_percent")
    if not isinstance(raw_r, (int, float)) or not isinstance(entry, (int, float)) or not isinstance(risk_pct, (int, float)) or risk_pct <= 0:
        return None
    cost_fraction = ROUND_TRIP_COST_BPS / 10000.0
    risk_fraction = float(risk_pct) / 100.0
    cost_r = cost_fraction / risk_fraction
    return float(raw_r) - cost_r


async def run_candidate_h_option_validator(provider, symbols: list[str], start_date: str, end_date: str, max_signals: int = 80):
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 16:
        raise ValueError("Candidate H option OOS blocks are limited to 16 calendar days")
    max_signals = max(1, min(int(max_signals), 120))

    candidate_fills: list[dict] = []
    errors: list[dict] = []
    underlying_counts = defaultdict(int)

    for raw in symbols:
        symbol = raw.upper().strip()
        if not symbol:
            continue
        try:
            rows = await _historical(provider, symbol, "5m", start - timedelta(days=5), end)
            rows = [r for r in rows if _ts(r[0]) and _ts(r[0]) <= end]
            atrs = _atr(rows, 14)
            for day, indices in sorted(_day_indices(rows).items()):
                d = datetime.fromisoformat(day).date()
                if d < start.date() or d > end.date():
                    continue
                signal = _failed_breakout_reversal(rows, indices, atrs)
                if not signal:
                    continue
                i, direction, stop, features = signal
                if direction != DIRECTION:
                    continue
                underlying_counts["signals"] += 1
                atr = atrs[i] if i < len(atrs) else 0.0
                if atr <= 0:
                    continue
                fill = _candidate_h_fill(rows, i, float(stop), float(atr))
                if fill["status"] == "UNFILLED":
                    underlying_counts["unfilled"] += 1
                    continue
                if fill["status"] != "FILLED":
                    underlying_counts["ambiguous_preentry"] += 1
                    continue
                underlying_counts["fills"] += 1
                signal_at = _ts(rows[i][0])
                candidate_fills.append({
                    "symbol": symbol,
                    "signal_at": signal_at.isoformat() if signal_at else str(rows[i][0]),
                    "features": features,
                    **fill,
                })
        except Exception as exc:
            errors.append({"symbol": symbol, "stage": "UNDERLYING_SIGNAL", "error": str(exc)})

    candidate_fills.sort(key=lambda x: str(x.get("fill_at", "")))
    candidate_fills = candidate_fills[:max_signals]
    master_rows = await _instrument_rows(symbols)
    contract_cache: dict[tuple[str, str, str], list[dict]] = {}
    trades: list[dict] = []

    for candidate in candidate_fills:
        symbol = candidate["symbol"]
        fill_when = datetime.fromisoformat(str(candidate["fill_at"]))
        try:
            expiry_date, expiry_dte = _select_expiry(master_rows, symbol, OPTION_TYPE, fill_when.date(), None)
            if expiry_date is None:
                errors.append({"symbol": symbol, "timestamp": candidate["fill_at"], "stage": "EXPIRY_SELECTION", "error": "No eligible listed CE expiry in current Groww master"})
                continue
            expiry = expiry_date.isoformat()
            key = (symbol, OPTION_TYPE, expiry)
            contracts = contract_cache.get(key)
            if contracts is None:
                contracts = _available_contracts(master_rows, symbol, expiry, OPTION_TYPE)
                contract_cache[key] = contracts
            if not contracts:
                errors.append({"symbol": symbol, "timestamp": candidate["fill_at"], "stage": "CONTRACT_SELECTION", "error": f"No CE contracts found for {expiry}"})
                continue
            underlying_entry = float(candidate["underlying_entry"])
            selected = min(contracts, key=lambda x: abs(float(x["strike"]) - underlying_entry))
            replay = await replay_option_trade(
                provider=provider,
                symbol=symbol,
                expiry=expiry,
                strike=float(selected["strike"]),
                option_type=OPTION_TYPE,
                trade_date=fill_when.date().isoformat(),
                entry_time=fill_when.strftime("%H:%M"),
                min_rr=1.0,
                resolved_contract=selected,
            )
            net_r = _net_r_from_replay(replay)
            scenario = (replay.get("target_scenarios") or {}).get("1.0R") or {}
            trades.append({
                **candidate,
                "action": "BUY CE",
                "expiry": expiry,
                "expiry_dte": expiry_dte,
                "expiry_selection": "NEAREST_LISTED_EXPIRY_ON_OR_AFTER_FILL_DATE",
                "strike": float(selected["strike"]),
                "strike_selection": "NEAREST_LISTED_STRIKE_TO_UNDERLYING_PULLBACK_FILL",
                "option_contract": selected.get("trading_symbol") or selected.get("groww_symbol"),
                "replay_status": replay.get("status"),
                "option_entry_at": replay.get("entry_at"),
                "option_entry": replay.get("option_entry"),
                "option_stop": replay.get("option_stop"),
                "premium_risk_percent": replay.get("premium_risk_percent"),
                "gross_1r_outcome": scenario.get("outcome"),
                "gross_r": scenario.get("r_multiple"),
                "option_exit": scenario.get("exit_price"),
                "option_exit_at": scenario.get("exit_at"),
                "net_r": round(net_r, 3) if isinstance(net_r, (int, float)) else None,
                "mfe_r": replay.get("mfe_r"),
                "mae_r": replay.get("mae_r"),
            })
        except Exception as exc:
            errors.append({"symbol": symbol, "timestamp": candidate.get("fill_at"), "stage": "OPTION_REPLAY", "error": str(exc)})

    resolved = [t for t in trades if isinstance(t.get("net_r"), (int, float))]
    rs = [float(t["net_r"]) for t in resolved]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    total_r = sum(rs)
    coverage = len(resolved) / len(candidate_fills) * 100.0 if candidate_fills else 0.0
    win_rate = len(wins) / len(resolved) * 100.0 if resolved else 0.0
    avg_r = total_r / len(resolved) if resolved else 0.0
    pf = _profit_factor(rs)
    pass_gate = (
        len(resolved) >= MIN_FILLED_SIGNALS
        and coverage >= MIN_OPTION_COVERAGE
        and win_rate >= MIN_WIN_RATE
        and avg_r >= MIN_AVG_R
        and pf >= MIN_PF
    )

    return {
        "mode": "CANDIDATE_H_TRUE_OPTION_PREMIUM_OOS_V1",
        "research_only": True,
        "production_rules_changed": False,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": symbols,
        "frozen_candidate": {
            "setup": SETUP_TYPE,
            "direction": DIRECTION,
            "execution": METHOD,
            "lookahead_bars": LOOKAHEAD_BARS,
            "pullback_atr": PULLBACK_ATR,
            "option_action": "BUY CE",
            "expiry": "nearest listed expiry on/after underlying fill date, max integrity DTE inherited from true-premium engine",
            "strike": "nearest listed strike to underlying pullback fill price",
            "option_entry": "next 5m option candle open after the underlying pullback-fill bar",
            "premium_stop": "existing frozen premium-risk schedule: 30% if premium<10, 25% if <30, else 20%",
            "target": "fixed 1.0 premium R",
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        },
        "underlying": dict(underlying_counts),
        "candidate_fills_selected": len(candidate_fills),
        "summary": {
            "option_replays": len(trades),
            "resolved": len(resolved),
            "coverage_pct": round(coverage, 1),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "average_r": round(avg_r, 3),
            "total_r": round(total_r, 3),
            "profit_factor": round(pf, 2),
            "decision": "PASS_OPTION_OOS" if pass_gate else "FAIL_OPTION_OOS",
        },
        "fixed_pass_gate": {
            "min_resolved": MIN_FILLED_SIGNALS,
            "min_coverage_pct": MIN_OPTION_COVERAGE,
            "min_win_rate": MIN_WIN_RATE,
            "min_average_r": MIN_AVG_R,
            "min_profit_factor": MIN_PF,
        },
        "trades": trades,
        "errors": errors,
        "limitations": [
            "Candidate H signal and 3-bar/0.25 ATR pullback execution are unchanged from the passed underlying OOS.",
            "Option action is frozen to BUY CE because Candidate H direction is LONG.",
            "Nearest listed expiry and nearest strike are selected mechanically; no expiry/strike alternatives are searched after results.",
            "Option entry is the next 5-minute option open after the underlying pullback-fill bar, which is conservative because intrabar fill chronology is unknown.",
            "Premium stop uses the pre-existing AlphaPilot premium-risk schedule and target is fixed at 1R; 10 bps round-trip cost is deducted in R terms.",
            "Same-candle premium stop/target collisions remain unresolved and are excluded rather than assigned favorable chronology.",
            "Current Groww instrument-master availability can limit older expired-contract coverage; missing contracts are reported, never fabricated.",
            "This stage cannot change production execution even if it passes.",
        ],
    }
