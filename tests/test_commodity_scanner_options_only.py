import asyncio
from unittest.mock import AsyncMock, patch

from app.commodity_scanner import commodity_mtf_scan


def test_commodity_scanner_never_exposes_futures_trade_as_execution_ready():
    contract={
        "trading_symbol":"COPPER30SEP26FUT",
        "groww_symbol":"MCX-COPPER-30Sep26-FUT",
        "exchange":"MCX",
        "segment":"COMMODITY",
    }

    candle_payload={"candles":[["2026-08-31T10:00:00+05:30",100,101,99,100,10]],"historical_source":"test"}
    frame={
        "signal":"BUY",
        "status":"SETUP",
        "alpha_score":80.0,
        "latest_candle_at":"2026-08-31T10:00:00+05:30",
        "rsi14":60.0,
        "entry":100.0,
        "stop_loss":98.0,
        "target1":103.0,
        "target2":104.0,
        "risk_reward":1.5,
    }

    async def run():
        with patch(
            "app.commodity_scanner.resolve_nearest_mcx_future",
            new=AsyncMock(return_value=contract),
        ), patch(
            "app.commodity_scanner.commodity_candles",
            new=AsyncMock(return_value=candle_payload),
        ), patch(
            "app.commodity_scanner.analyze_commodity_candles",
            return_value=frame,
        ), patch(
            "app.commodity_scanner._fresh_enough",
            return_value=(True,1.0,"2026-08-31T10:00:00+05:30"),
        ), patch(
            "app.commodity_scanner.mcx_session_status",
            return_value={"is_open":True,"status":"OPEN"},
        ), patch(
            "app.commodity_scanner.commodity_quote",
            new=AsyncMock(return_value={"last_price":100.2}),
        ):
            return await commodity_mtf_scan(object(),"COPPER",1.5)

    result=asyncio.run(run())
    assert result["underlying_signal_ready"] is True
    assert result["directional_bias"]=="BULLISH"
    assert result["underlying_action"]=="BUY"
    assert result["action"]=="NO TRADE"
    assert result["execution_ready"] is False
    assert result["reference_only"] is True
    assert result["execution_eligible"] is False
    assert result["trade_instrument"]=="OPTIONS"
    assert result["options_only_policy"]["futures_execution_allowed"] is False
    assert result["status"]=="REFERENCE_READY"
