from datetime import datetime
from zoneinfo import ZoneInfo

from app.crude_oil_mini_option_expression_v1 import build_option_expression

IST = ZoneInfo("Asia/Kolkata")
CLICK = datetime(2026, 9, 4, 14, 55, tzinfo=IST)


def _row(option_type, strike, premium, volume=1000, oi=500, lot_size=10):
    return {
        "trading_symbol": f"CRUDEOILM17SEP26{int(strike)}{option_type}",
        "expiry_date": "2026-09-17",
        "strike": strike,
        "option_type": option_type,
        "lot_size": lot_size,
        "sample_bucket_at": "2026-09-04T14:50:00+05:30",
        "observed_at": "2026-09-04T14:50:01+05:30",
        "collected_at": "2026-09-04T14:50:02+05:30",
        "underlying_price": 8574,
        "last_price": premium,
        "volume": volume,
        "open_interest": oi,
        "bid_price": None,
        "ask_price": None,
    }


def _positioning(rows):
    return {
        "status": "AVAILABLE",
        "nearest_expiry": "2026-09-17",
        "underlying_price": 8574,
        "contracts": rows,
    }


def test_no_trade_does_not_create_expression():
    assert build_option_expression(action="NO_TRADE", option_positioning=_positioning([_row("CE", 8600, 300)]), click_at=CLICK) is None


def test_buy_ce_selects_nearest_atm_and_respects_cap():
    result = build_option_expression(
        action="BUY_CE",
        option_positioning=_positioning([_row("CE", 8500, 350), _row("CE", 8600, 300), _row("PE", 8600, 330)]),
        click_at=CLICK,
    )
    assert result["status"] == "EXPRESSED"
    assert result["option_type"] == "CE"
    assert result["strike"] == 8600
    assert result["lot_size"] == 10
    assert result["lots"] == 5
    assert result["estimated_premium_outlay"] == 15000
    assert result["broker_order_placement_enabled"] is False
    assert result["capital_committed"] == 0


def test_future_snapshot_is_rejected():
    row = _row("PE", 8600, 330)
    row["collected_at"] = "2026-09-04T14:56:00+05:30"
    result = build_option_expression(action="BUY_PE", option_positioning=_positioning([row]), click_at=CLICK)
    assert result["status"] == "UNAVAILABLE"


def test_contract_above_cap_is_rejected():
    result = build_option_expression(
        action="BUY_CE",
        option_positioning=_positioning([_row("CE", 8600, 1600, lot_size=10)]),
        click_at=CLICK,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "NO_ELIGIBLE_CONTRACT_WITHIN_CAPITAL_CAP"
