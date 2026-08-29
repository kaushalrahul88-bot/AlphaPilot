import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.commodity_option_candle_collector import (
    DEFAULT_STRIKES_PER_TYPE,
    _records,
    collect_copper_option_candles,
)


IST=ZoneInfo("Asia/Kolkata")


class UnderlyingStore:
    def __init__(self, rows):
        self.rows=rows
        self.initialized=False
        self.read_args=None

    async def initialize(self):
        self.initialized=True

    async def read_symbol(self, symbol, timeframe_minutes, start, end):
        self.read_args=(symbol,timeframe_minutes,start,end)
        return self.rows


class OptionStore:
    def __init__(self):
        self.initialized=False
        self.rows=[]

    async def initialize(self):
        self.initialized=True

    async def upsert(self, records):
        self.rows.extend(records)
        return len(records)

    async def status(self, underlying_symbol="COPPER"):
        return {"enabled":True,"underlying_symbol":underlying_symbol,"series":[]}


def contract(option_type, strike):
    return {
        "underlying":"COPPER",
        "exchange":"MCX",
        "segment":"COMMODITY",
        "option_type":option_type,
        "expiry":"2026-09-23",
        "strike":float(strike),
        "groww_symbol":f"MCX-COPPER-23Sep26-{strike}-{option_type}",
        "trading_symbol":f"COPPER23SEP26{strike}{option_type}",
        "lot_size":2500,
    }


def test_option_records_are_idempotency_ready_and_research_data_only():
    c=contract("PE",1150)
    rows=[
        ["2026-08-31T10:00:00+05:30",5,6,4,5.5,10],
        ["2026-08-31T10:00:00+05:30",5.1,6.1,4.1,5.6,12],
    ]
    records=_records(c,rows,datetime(2026,8,31,23,45,tzinfo=IST))
    assert len(records)==1
    assert records[0]["underlying_symbol"]=="COPPER"
    assert records[0]["option_type"]=="PE"
    assert records[0]["strike"] > 0
    assert records[0]["timeframe_minutes"]==5


def test_collector_fails_closed_without_stored_underlying_session():
    async def run():
        underlying=UnderlyingStore([])
        option=OptionStore()
        with patch(
            "app.commodity_option_candle_collector.fetch_mcx_option_master",
            new=AsyncMock(),
        ) as master:
            result=await collect_copper_option_candles(
                object(),underlying,option,
                now=datetime(2026,8,31,23,45,tzinfo=IST),
            )
            return result,master,underlying,option

    result,master,underlying,option=asyncio.run(run())
    assert result["status"]=="NO_UNDERLYING_CANDLES"
    assert result["upserted"]==0
    assert underlying.initialized is True
    assert option.initialized is True
    master.assert_not_awaited()


def test_collector_uses_stored_close_and_collects_both_option_types():
    underlying_rows=[
        ["2026-08-31T09:00:00+05:30",1140,1145,1138,1142,100],
        ["2026-08-31T18:00:00+05:30",1150,1160,1148,1155,120],
    ]
    ce=contract("CE",1150)
    pe=contract("PE",1150)
    master=[ce,pe]

    def ranked(rows,symbol,trade_date,underlying_price,option_type,max_strikes):
        assert rows==master
        assert symbol=="COPPER"
        assert underlying_price==1155.0
        assert max_strikes==DEFAULT_STRIKES_PER_TYPE
        return [ce] if option_type=="CE" else [pe]

    async def history(provider,selected,trade_date):
        price=6.0 if selected["option_type"]=="CE" else 7.0
        return {
            "status":"AVAILABLE",
            "candles":[
                ["2026-08-31T10:00:00+05:30",price,price+1,price-1,price+.5,20],
                ["2026-08-31T10:05:00+05:30",price+.5,price+1,price,price+.75,25],
            ],
        }

    async def run():
        underlying=UnderlyingStore(underlying_rows)
        option=OptionStore()
        with patch(
            "app.commodity_option_candle_collector.fetch_mcx_option_master",
            new=AsyncMock(return_value=master),
        ), patch(
            "app.commodity_option_candle_collector.ranked_mcx_option_contracts",
            side_effect=ranked,
        ), patch(
            "app.commodity_option_candle_collector.fetch_mcx_option_day",
            new=AsyncMock(side_effect=history),
        ):
            result=await collect_copper_option_candles(
                object(),underlying,option,
                now=datetime(2026,8,31,23,45,tzinfo=IST),
            )
            return result,option

    result,option=asyncio.run(run())
    assert result["status"]=="COLLECTED"
    assert result["underlying_close"]==1155.0
    assert result["session_low"]==1138.0
    assert result["session_high"]==1160.0
    assert result["contracts_requested"]==2
    assert result["contracts_with_candles"]==2
    assert result["upserted"]==4
    assert {row["option_type"] for row in option.rows}=={"CE","PE"}
    assert result["production_rules_changed"] is False
    assert result["live_execution_enabled"] is False


def test_collector_reports_provider_errors_as_partial_or_no_option_candles():
    underlying_rows=[["2026-08-31T18:00:00+05:30",1150,1160,1148,1155,120]]
    ce=contract("CE",1150)
    pe=contract("PE",1150)

    def ranked(rows,symbol,trade_date,underlying_price,option_type,max_strikes):
        return [ce] if option_type=="CE" else [pe]

    async def history(provider,selected,trade_date):
        if selected["option_type"]=="PE":
            raise RuntimeError("upstream unavailable")
        return {"status":"AVAILABLE","candles":[
            ["2026-08-31T10:00:00+05:30",6,7,5,6.5,20],
        ]}

    async def run():
        underlying=UnderlyingStore(underlying_rows)
        option=OptionStore()
        with patch(
            "app.commodity_option_candle_collector.fetch_mcx_option_master",
            new=AsyncMock(return_value=[ce,pe]),
        ), patch(
            "app.commodity_option_candle_collector.ranked_mcx_option_contracts",
            side_effect=ranked,
        ), patch(
            "app.commodity_option_candle_collector.fetch_mcx_option_day",
            new=AsyncMock(side_effect=history),
        ):
            return await collect_copper_option_candles(
                object(),underlying,option,
                now=datetime(2026,8,31,23,45,tzinfo=IST),
            )

    result=asyncio.run(run())
    assert result["status"]=="PARTIAL"
    assert result["contracts_with_candles"]==1
    assert result["data_errors"]==1
