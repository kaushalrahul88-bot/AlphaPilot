from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .commodity_backtest import _fetch_chunked, _ts
from .commodity_benchmarks import benchmark_confirmation, fetch_benchmark_candles
from .commodity_click_brain import _valid_rows, evaluate_commodity_click, market_brain_audit
from .commodity_mtf import TIMEFRAMES, completed_mtf_snapshot, completed_rows
from .commodity_next_session import build_next_session_plan
from .commodity_option_history import fetch_mcx_option_master, select_mcx_option_contract
from .commodities import mcx_session_status, resolve_nearest_mcx_future
from .options_only_policy import assert_option_action, assert_option_contract, options_only_policy


IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = ("CRUDEOIL", "NATURALGAS")
PREMIUM_RISK_REWARD = 1.5


def _completed_rows(rows, click_at, interval_minutes):
    return completed_rows(rows, click_at, interval_minutes)


def _merge_rows(*groups):
    deduplicated = {}
    for group in groups:
        for row in _valid_rows(group):
            deduplicated[row[0].isoformat()] = row
    return [deduplicated[key] for key in sorted(deduplicated)]


async def _fetch_live_rows(provider, contract, fetch_start, click_at, expected_previous):
    previous_start = datetime.combine(expected_previous, time(9, 0), tzinfo=IST)
    previous_end = datetime.combine(expected_previous, time(23, 30), tzinfo=IST)
    rows_by_timeframe = {}
    targeted_counts = {}
    for timeframe, minutes in TIMEFRAMES.items():
        combined = await _fetch_chunked(provider, contract, minutes, fetch_start, click_at)
        targeted = await _fetch_chunked(provider, contract, minutes, previous_start, previous_end)
        rows_by_timeframe[timeframe] = _merge_rows(combined, targeted)
        targeted_counts[timeframe] = len(_valid_rows(targeted))
    return rows_by_timeframe, targeted_counts


def _expected_previous_weekday(target_date):
    candidate = target_date - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _previous_session_state(rows, target_date):
    grouped = {}
    for row in _valid_rows(rows):
        stamp = row[0]
        if stamp.date() >= target_date or not (time(9, 0) <= stamp.time() <= time(23, 30)):
            continue
        grouped.setdefault(stamp.date(), []).append(row)
    expected = _expected_previous_weekday(target_date)
    latest = max(grouped) if grouped else None
    values = grouped.get(expected, [])
    last = values[-1][0] if values else None
    checks = {
        "expected_session_present": bool(values),
        "expected_session_is_latest_observed": latest == expected,
        "minimum_previous_candles": len(values) >= 100,
        "previous_session_reaches_close": last is not None and last.time() >= time(23, 20),
    }
    return {
        "expected_date": expected,
        "latest_observed_date": latest,
        "candles": len(values),
        "last_at": last,
        "checks": checks,
        "complete": all(checks.values()),
    }


def _previous_complete_session(rows, target_date):
    state = _previous_session_state(rows, target_date)
    return state["expected_date"] if state["complete"] else None


def _live_mtf(symbol, rows_by_timeframe, click_at):
    return completed_mtf_snapshot(symbol, rows_by_timeframe, click_at, PREMIUM_RISK_REWARD)


def _quote_payload(body):
    if not isinstance(body, dict):
        return None
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        return None
    for key in ("last_price", "ltp", "last_traded_price"):
        try:
            value = float(payload.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return payload, value
    return None


async def fetch_live_mcx_option_quote(provider, contract):
    assert_option_contract(contract)
    throttle = getattr(provider, "_throttle", None)
    if callable(throttle):
        await throttle()
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            f"{provider.BASE_URL}/v1/live-data/quote",
            headers=await provider._headers(),
            params={
                "exchange": "MCX",
                "segment": "COMMODITY",
                "trading_symbol": contract["trading_symbol"],
            },
        )
    response.raise_for_status()
    parsed = _quote_payload(response.json())
    if parsed is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "Exact MCX option quote has no positive live premium.",
            "contract": contract,
        }
    payload, premium = parsed
    observed_at = datetime.now(IST)
    return {
        "status": "AVAILABLE",
        "provider": "GROWW",
        "data_status": "LIVE",
        "premium": round(premium, 4),
        "observed_at": observed_at.isoformat(),
        "contract": contract,
        "source": "GROWW_LIVE_MCX_OPTION_QUOTE",
        "payload_keys": sorted(payload.keys()),
    }


def _data_quality(
    symbol,
    contract,
    rows_by_timeframe,
    current_rows,
    comparison_rows,
    previous_state,
    targeted_previous_counts,
    click_at,
):
    click = _ts(click_at)
    first = current_rows[0][0] if current_rows else None
    last = current_rows[-1][0] if current_rows else None
    comparison_dates = {row[0].date() for row in comparison_rows if row[5] > 0}
    checks = {
        "previous_complete_session": previous_state["complete"],
        "current_session_started": first is not None and first.time() <= time(9, 15),
        "minimum_current_candles": len(current_rows) >= 4,
        "current_volume": sum(row[5] for row in current_rows) > 0,
        "comparison_sessions": len(comparison_dates) >= 5,
        "no_future_or_open_5m_candles": all(row[0] + timedelta(minutes=5) <= click for row in current_rows),
    }
    return {
        "symbol": symbol,
        "contract": contract.get("trading_symbol"),
        "status": "VALID" if all(checks.values()) else "DATA_NOT_READY",
        "checks": checks,
        "candles": {key: len(value) for key, value in rows_by_timeframe.items()},
        "targeted_previous_fetch_candles": dict(targeted_previous_counts),
        "expected_previous_session": previous_state["expected_date"].isoformat(),
        "latest_previous_observed_session": previous_state["latest_observed_date"].isoformat() if previous_state["latest_observed_date"] else None,
        "previous_session_candles": previous_state["candles"],
        "previous_session_last_at": previous_state["last_at"].isoformat() if previous_state["last_at"] else None,
        "previous_session_checks": previous_state["checks"],
        "current_completed_5m_candles": len(current_rows),
        "current_first_at": first.isoformat() if first else None,
        "current_last_at": last.isoformat() if last else None,
        "comparison_sessions": len(comparison_dates),
    }


def _blocked_result(symbol, click, status, reason, quality=None):
    return {
        "symbol": symbol,
        "click_at": click.isoformat(),
        "decision_status": status,
        "action": "NO TRADE",
        "reason": reason,
        "data_quality": quality,
        "research_only": True,
        "live_execution_enabled": False,
    }


async def run_commodity_live_scan(provider, now=None):
    click = _ts(now or datetime.now(IST))
    target_date = click.date()
    expected_previous = _expected_previous_weekday(target_date)
    session = mcx_session_status(click)
    fetch_start = datetime.combine(target_date - timedelta(days=16), time(9, 0), tzinfo=IST)
    benchmark_start = datetime.combine(target_date, time(0, 0), tzinfo=IST)
    results = []
    option_master = None

    for symbol in SYMBOLS:
        try:
            contract = await resolve_nearest_mcx_future(symbol)
            rows_by_timeframe, targeted_previous_counts = await _fetch_live_rows(
                provider, contract, fetch_start, click, expected_previous,
            )
            completed_5m = _completed_rows(rows_by_timeframe["5m"], click, 5)
            previous_state = _previous_session_state(completed_5m, target_date)
            previous_date = previous_state["expected_date"] if previous_state["complete"] else None
            current_rows = [row for row in completed_5m if row[0].date() == target_date]
            comparison_rows = [row for row in completed_5m if row[0].date() < target_date]
            quality = _data_quality(
                symbol,
                contract,
                rows_by_timeframe,
                current_rows,
                comparison_rows,
                previous_state,
                targeted_previous_counts,
                click,
            )
            if not session["is_open"]:
                results.append(_blocked_result(symbol, click, "MARKET_CLOSED", "MCX session is closed.", quality))
                continue
            if quality["status"] != "VALID":
                results.append(_blocked_result(
                    symbol,
                    click,
                    "DATA_NOT_READY",
                    "The immediately preceding weekday MCX session is missing or incomplete; older sessions are not eligible.",
                    quality,
                ))
                continue

            previous = build_next_session_plan(
                symbol, completed_5m, previous_date, target_date, contract.get("tick_size"),
            )
            frames, plan, mtf, frame_freshness = _live_mtf(symbol, rows_by_timeframe, click)
            benchmark_payload = await fetch_benchmark_candles(symbol, benchmark_start, click)
            benchmark_rows = _completed_rows(benchmark_payload.get("candles", []), click, 5)
            benchmark = benchmark_confirmation(symbol, benchmark_rows, click)
            directional = evaluate_commodity_click(
                symbol=symbol,
                click_at=click,
                previous_plan=previous,
                mtf_snapshot=mtf,
                current_rows=current_rows,
                comparison_rows=comparison_rows,
                benchmark=benchmark,
                option_premium=None,
                premium_risk_reward=PREMIUM_RISK_REWARD,
                require_option_premium=False,
            )

            option_quote = {"status": "NOT_REQUESTED", "reason": "Directional market gates did not all pass."}
            strict = None
            decision_status = directional["status"]
            underlying_action = str(directional.get("action") or "NO TRADE").upper()
            directional_bias = (
                "BULLISH" if underlying_action == "BUY"
                else "BEARISH" if underlying_action == "SELL"
                else "NEUTRAL"
            )
            action = "NO TRADE"
            option_intent = None
            if directional["status"] == "READY" and plan:
                option_intent = assert_option_action(
                    "BUY CE" if previous.get("underlying_direction") == "BULLISH" else "BUY PE"
                )
                option_type = option_intent.split()[-1]
                if option_master is None:
                    option_master = await fetch_mcx_option_master(SYMBOLS)
                selected = select_mcx_option_contract(
                    [row for row in option_master if row.get("buy_allowed")],
                    symbol,
                    target_date,
                    current_rows[-1][4],
                    option_type,
                )
                if selected is None:
                    option_quote = {"status": "CONTRACT_NOT_FOUND", "option_type": option_type}
                else:
                    try:
                        assert_option_contract(selected)
                        option_quote = await fetch_live_mcx_option_quote(provider, selected)
                    except Exception as exc:
                        option_quote = {
                            "status": "QUOTE_ERROR",
                            "contract": selected,
                            "error": f"{exc.__class__.__name__}: {str(exc)[:180]}",
                        }
                premium = option_quote.get("premium") if option_quote.get("status") == "AVAILABLE" else None
                strict = evaluate_commodity_click(
                    symbol=symbol,
                    click_at=click,
                    previous_plan=previous,
                    mtf_snapshot=mtf,
                    current_rows=current_rows,
                    comparison_rows=comparison_rows,
                    benchmark=benchmark,
                    option_premium=premium,
                    premium_risk_reward=PREMIUM_RISK_REWARD,
                    require_option_premium=True,
                )
                if strict["status"] == "READY":
                    decision_status = "EXECUTABLE_READY"
                    action = option_intent
                else:
                    decision_status = "DIRECTIONAL_READY"

            gates = (strict or directional)["gates"]
            blockers = (strict or directional)["blockers"]
            results.append({
                "symbol": symbol,
                "click_at": click.isoformat(),
                "decision_status": decision_status,
                "action": action,
                "option_intent": option_intent,
                "underlying_action": underlying_action,
                "directional_bias": directional_bias,
                "previous_session": previous_date.isoformat(),
                "previous_plan": previous,
                "current_mtf_action": mtf.get("action"),
                "current_mtf_strength": mtf.get("alpha_score"),
                "current_completed_5m_candles": len(current_rows),
                "frame_freshness": frame_freshness,
                "timeframe_signals": {key: value.get("signal") for key, value in frames.items()},
                "benchmark": benchmark,
                "underlying_setup": {
                    "entry": plan.get("entry"),
                    "stop_loss": plan.get("stop"),
                    "target1": plan.get("target1"),
                    "risk_reward": PREMIUM_RISK_REWARD,
                    "reference_only": True,
                    "execution_eligible": False,
                } if plan else None,
                "trade_instrument": "OPTIONS",
                "option_quote": option_quote,
                "premium_setup": (strict or {}).get("premium_setup"),
                "gates": gates,
                "blockers": blockers,
                "market_brain_audit": market_brain_audit(previous, frames, click, gates),
                "data_quality": quality,
                "research_only": True,
                "live_execution_enabled": False,
            })
        except Exception as exc:
            results.append(_blocked_result(
                symbol,
                click,
                "DATA_ERROR",
                f"{exc.__class__.__name__}: {str(exc)[:180]}",
            ))

    return {
        "mode": "COMMODITY_LIVE_PROTOTYPE_V1",
        "generated_at": datetime.now(IST).isoformat(),
        "click_at": click.isoformat(),
        "target_date": target_date.isoformat(),
        "market_session": session,
        "symbols": list(SYMBOLS),
        "premium_risk_reward": PREMIUM_RISK_REWARD,
        "trade_instrument": "OPTIONS",
        "options_only_policy": options_only_policy(),
        "results": results,
        "summary": {
            "executable_ready": sum(row.get("decision_status") == "EXECUTABLE_READY" for row in results),
            "directional_ready": sum(row.get("decision_status") == "DIRECTIONAL_READY" for row in results),
            "wait": sum(row.get("decision_status") == "WAIT" for row in results),
            "no_trade": sum(row.get("decision_status") == "NO_TRADE" for row in results),
            "data_not_ready": sum(row.get("decision_status") in {"DATA_NOT_READY", "DATA_ERROR"} for row in results),
        },
        "readiness_definition": {
            "DIRECTIONAL_READY": "Underlying directional context passed, but no option trade action is emitted until an exact option contract and verified live premium pass the strict option gates.",
            "EXECUTABLE_READY": "An exact buy-allowed MCX option contract returned a verified positive live premium and all strict option gates passed. The emitted action is BUY CE or BUY PE only.",
        },
        "research_only": True,
        "production_rules_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
        "order_endpoint_called": False,
    }
