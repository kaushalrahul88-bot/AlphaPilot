from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodities import mcx_session_status
from .crude_oil_mini_live_click import evaluate_live_current_mind
from .crude_oil_mini_live_inputs import read_live_crude_inputs
from .crude_oil_mini_option_expression_v1 import build_option_expression
from .crude_oil_mini_option_premium_memory_v1 import read_crude_oil_mini_premium_memory
from .crude_oil_mini_pit_candles import (
    CrudeOilMiniPITStore,
    collect_crude_oil_mini_pit_candles,
    read_crude_oil_mini_pit_candles,
    resolve_current_crude_oil_mini_future,
)
from .crude_oil_pit_context_probe import probe_crude_oil_pit_context
from .providers.factory import get_provider

IST = ZoneInfo("Asia/Kolkata")
SAFE_EXECUTION = {
    "paper_signal_only": True,
    "live_execution_enabled": False,
    "broker_order_placement_enabled": False,
    "capital_committed": 0,
}


def register_crude_oil_mini_manual_routes(app, settings) -> None:
    @app.post("/v1/crude-oil-mini/current-mind/click")
    async def crude_oil_mini_manual_click():
        click = datetime.now(IST)
        session = mcx_session_status(click)
        if not session.get("is_open"):
            return {
                "status": "SKIPPED_MARKET_CLOSED",
                "click_at": click.isoformat(),
                "point_in_time": True,
                "product": "CRUDE_OIL_MINI",
                "trade_instrument": "OPTIONS_ONLY",
                "market_session": session,
                "execution": SAFE_EXECUTION,
            }
        if not str(settings.database_url or "").strip():
            return {
                "status": "DATA_ERROR",
                "reason": "CRUDEOILM PIT candle storage is not configured",
                "click_at": click.isoformat(),
                "point_in_time": True,
                "market_session": session,
                "execution": SAFE_EXECUTION,
            }

        try:
            store = CrudeOilMiniPITStore(settings.database_url)
            collection = await collect_crude_oil_mini_pit_candles(
                get_provider(settings), store, click
            )
            candles = await read_crude_oil_mini_pit_candles(store, click, lookback_days=7)
            if not candles:
                raise RuntimeError("No point-in-time-safe CRUDEOILM candles are available")
            contract = await resolve_current_crude_oil_mini_future(click)
            live_inputs = await read_live_crude_inputs(settings.database_url, click_at=click)
            context = await probe_crude_oil_pit_context(
                start=(click - timedelta(days=7)).isoformat(),
                end=(click + timedelta(minutes=5)).isoformat(),
            )
            result = evaluate_live_current_mind(
                candles,
                contract=contract,
                global_context_probe=context,
                live_inputs=live_inputs,
                click_at=click,
                scheduled_slot_at=click,
            )
            action = str((result.get("current_mind") or {}).get("action") or "NO_TRADE")
            option_expression = build_option_expression(
                action=action,
                option_positioning=(live_inputs or {}).get("option_positioning"),
                click_at=click,
            )

            # Descriptive premium memory is downstream research only. A memory
            # read failure must never block or alter the frozen Current Mind.
            try:
                premium_memory = await read_crude_oil_mini_premium_memory(
                    settings.database_url,
                    as_of=click,
                    lookback_days=7,
                )
            except Exception as exc:
                premium_memory = {
                    "status": "UNAVAILABLE",
                    "model_id": "CRUDE_OIL_MINI_OPTION_PREMIUM_MEMORY_V1",
                    "reason": f"{exc.__class__.__name__}: {str(exc)[:240]}",
                    "risk_translation_effect": "NONE",
                    "current_mind_effect": "NONE",
                    "promotion_eligible": False,
                }

            result["status"] = "EVALUATED"
            result["market_session"] = session
            result["data"]["candle_collection"] = collection
            result["data"]["candle_source"] = "POSTGRES_CRUDEOILM_PIT_FIRST_SEEN"
            result["data"]["expensive_180_day_live_refetch_used"] = False
            result["data"]["option_premium_memory"] = premium_memory
            result["manual_dashboard_click"] = True
            result["execution"] = {
                **result.get("execution", {}),
                **SAFE_EXECUTION,
                "option_expression": option_expression,
            }
            return result
        except Exception as exc:
            return {
                "status": "DATA_ERROR",
                "reason": f"{exc.__class__.__name__}: {str(exc)[:1000]}",
                "click_at": click.isoformat(),
                "point_in_time": True,
                "product": "CRUDE_OIL_MINI",
                "trade_instrument": "OPTIONS_ONLY",
                "market_session": session,
                "manual_dashboard_click": True,
                "execution": SAFE_EXECUTION,
            }
