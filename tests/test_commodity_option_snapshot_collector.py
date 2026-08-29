import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.commodity_option_snapshot_collector import (
    _bucket_5m,
    _normalize_quote_body,
    collect_copper_option_snapshots,
)


IST=ZoneInfo("Asia/Kolkata")


class UnderlyingStore:
    def __init__(self, rows):
        self.rows=rows
        self.initialized=False

    async def initialize(self):
        self.initialized=True

    async def read_symbol(self, symbol, timeframe_minutes, start, end):
        return self.rows


class SnapshotStore:
    def __init__(self):
        self.initialized=False
        self.records=[]

    async def initialize(self):
        self.initialized=True

    async def upsert(self, records):
        self.records.extend(records)
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


def test_five_minute_bucket_uses_actual_observation_time():
    stamp=datetime(2026,8,31,10,7,42,tzinfo=IST)
    assert _bucket_5m(stamp).isoformat()=="2026-08-31T10:05:00+05:30"


def test_quote_normalization_keeps_ltp_and_available_market_fields():
    result=_normalize_quote_body({
        "status":"SUCCESS",
        "payload":{
            "last_price":6.25,
            "volume":120,
            "open_interest":340,
            "market_depth":{
                "buy":[{"price":6.20}],
                "sell":[{"price":6.30}],
            },
        },
    })
    assert result["last_price"]==6.25
    assert result["volume"]==120
    assert result["open_interest"]==340
    assert result["bid_price"]==6.20
    assert result["ask_price"]==6.30


def test_market_closed_never_calls_provider_or_storage():
    async def run():
        underlying=UnderlyingStore([])
        snapshots=SnapshotStore()
        with patch(
            "app.commodity_option_snapshot_collector._current_master",
            new=AsyncMock(),
        ) as master:
            result=await collect_copper_option_snapshots(
                object(),underlying,snapshots,
                now=datetime(2026,8,29,12,0,tzinfo=IST),
            )
            return result,master,underlying,snapshots

    result,master,underlying,snapshots=asyncio.run(run())
    assert result["status"]=="MARKET_CLOSED"
    assert result["snapshots"]==0
    assert underlying.initialized is False
    assert snapshots.initialized is False
    master.assert_not_awaited()


def test_collector_requires_fresh_same_day_underlying_candle():
    async def run():
        underlying=UnderlyingStore([
            ["2026-08-31T09:00:00+05:30",1380,1382,1379,1381,100],
        ])
        snapshots=SnapshotStore()
        with patch(
            "app.commodity_option_snapshot_collector._current_master",
            new=AsyncMock(),
        ) as master:
            result=await collect_copper_option_snapshots(
                object(),underlying,snapshots,
                now=datetime(2026,8,31,10,0,tzinfo=IST),
            )
            return result,master

    result,master=asyncio.run(run())
    assert result["status"]=="STALE_UNDERLYING_CANDLE"
    assert result["snapshots"]==0
    master.assert_not_awaited()


def test_live_snapshot_collection_persists_both_ce_and_pe_without_fake_ohlc():
    underlying=UnderlyingStore([
        ["2026-08-31T09:55:00+05:30",1380,1384,1379,1381.5,100],
    ])
    snapshots=SnapshotStore()
    ce=[contract("CE",strike) for strike in (1380,1390,1400)]
    pe=[contract("PE",strike) for strike in (1380,1370,1360)]
    master=ce+pe

    def ranked(rows,symbol,trade_date,underlying_price,option_type,max_strikes):
        assert rows==master
        assert symbol=="COPPER"
        assert trade_date.isoformat()=="2026-08-31"
        assert underlying_price==1381.5
        assert max_strikes==10
        return ce if option_type=="CE" else pe

    async def quote(provider,selected):
        base=6.0 if selected["option_type"]=="CE" else 7.0
        return {
            "last_price":base+selected["strike"]/10000,
            "volume":100,
            "open_interest":200,
            "bid_price":base-.1,
            "ask_price":base+.1,
            "payload":{"last_price":base},
        }

    async def run():
        with patch(
            "app.commodity_option_snapshot_collector._current_master",
            new=AsyncMock(return_value=master),
        ), patch(
            "app.commodity_option_snapshot_collector.ranked_mcx_option_contracts",
            side_effect=ranked,
        ), patch(
            "app.commodity_option_snapshot_collector.fetch_mcx_option_live_quote",
            new=AsyncMock(side_effect=quote),
        ):
            return await collect_copper_option_snapshots(
                object(),underlying,snapshots,
                now=datetime(2026,8,31,10,2,tzinfo=IST),
            )

    result=asyncio.run(run())
    assert result["status"]=="COLLECTED"
    assert result["data_type"]=="LIVE_5M_LTP_SNAPSHOTS_NOT_OHLC"
    assert result["sample_bucket_at"]=="2026-08-31T10:00:00+05:30"
    assert result["snapshots"]==6
    assert result["ce_snapshots"]==3
    assert result["pe_snapshots"]==3
    assert result["quality"]["pass"] is True
    assert result["production_rules_changed"] is False
    assert result["live_execution_enabled"] is False
    assert len(snapshots.records)==6
    assert all("last_price" in row for row in snapshots.records)
    assert all("open" not in row and "high" not in row and "low" not in row for row in snapshots.records)


def test_snapshot_quality_fails_closed_when_one_option_side_is_missing():
    underlying=UnderlyingStore([
        ["2026-08-31T09:55:00+05:30",1380,1384,1379,1381.5,100],
    ])
    snapshots=SnapshotStore()
    ce=[contract("CE",strike) for strike in (1380,1390,1400)]
    pe=[contract("PE",strike) for strike in (1380,1370,1360)]

    def ranked(rows,symbol,trade_date,underlying_price,option_type,max_strikes):
        return ce if option_type=="CE" else pe

    async def quote(provider,selected):
        if selected["option_type"]=="PE":
            raise RuntimeError("quote unavailable")
        return {"last_price":6.5,"payload":{"last_price":6.5}}

    async def run():
        with patch(
            "app.commodity_option_snapshot_collector._current_master",
            new=AsyncMock(return_value=ce+pe),
        ), patch(
            "app.commodity_option_snapshot_collector.ranked_mcx_option_contracts",
            side_effect=ranked,
        ), patch(
            "app.commodity_option_snapshot_collector.fetch_mcx_option_live_quote",
            new=AsyncMock(side_effect=quote),
        ):
            return await collect_copper_option_snapshots(
                object(),underlying,snapshots,
                now=datetime(2026,8,31,10,2,tzinfo=IST),
            )

    result=asyncio.run(run())
    assert result["status"]=="INSUFFICIENT_OPTION_QUOTES"
    assert result["ce_snapshots"]==3
    assert result["pe_snapshots"]==0
    assert result["quality"]["pass"] is False
