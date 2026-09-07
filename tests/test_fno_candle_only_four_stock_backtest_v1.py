from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.fno_candle_only_four_stock_backtest_v1 import (
    FROZEN_STOCKS,
    STOCKS,
    _barrier,
    architecture_contract,
    common_last_20_sessions,
    deterministic_clicks,
    last_20_sessions_by_stock,
)

IST = ZoneInfo("Asia/Kolkata")


def _bar(day, hour=9, minute=15, o=100, h=101, l=99, c=100, v=1000):
    return [
        datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).isoformat(),
        o,
        h,
        l,
        c,
        v,
    ]


def _weekday_sessions(count=22):
    sessions = []
    current = date(2026, 8, 1)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return sessions


def test_frozen_basket_has_four_unrelated_categories():
    assert STOCKS == ("ONGC", "LTIM", "SBIN", "SUNPHARMA")
    assert len({category for _, category in FROZEN_STOCKS}) == 4


def test_random_clicks_are_fixed_unique_and_5m_aligned():
    day = date(2026, 9, 4)
    a = deterministic_clicks(day)
    b = deterministic_clicks(day)
    assert a == b and len(a) == 20 and len(set(a)) == 20
    for stamp in a:
        local = stamp.astimezone(IST)
        assert (local.hour, local.minute) >= (9, 30)
        assert (local.hour, local.minute) <= (14, 0)
        assert local.minute % 5 == 0


def test_per_stock_sessions_do_not_require_cross_stock_intersection():
    sessions = _weekday_sessions()
    histories = {symbol: [_bar(day) for day in sessions] for symbol in STOCKS}
    missing_ltim_day = sessions[-5]
    histories["LTIM"] = [
        row for row in histories["LTIM"]
        if datetime.fromisoformat(row[0]).date() != missing_ltim_day
    ]

    selected = last_20_sessions_by_stock(histories)
    assert all(len(days) == 20 for days in selected.values())
    assert selected["ONGC"] == sessions[-20:]
    assert selected["LTIM"] != selected["ONGC"]
    assert missing_ltim_day not in selected["LTIM"]
    assert sessions[1] in selected["LTIM"]


def test_common_sessions_remain_diagnostic_only():
    sessions = _weekday_sessions()
    histories = {symbol: [_bar(day) for day in sessions] for symbol in STOCKS}
    histories["LTIM"] = histories["LTIM"][1:]
    assert common_last_20_sessions(histories) == sessions[-20:]


def test_same_bar_sl_t1_is_not_given_favourable_ordering():
    click = datetime(2026, 9, 4, 10, 0, tzinfo=IST).astimezone(ZoneInfo("UTC"))
    bars = [
        _bar(date(2026, 9, 4), 10, 0, 100, 103, 97, 101),
        _bar(date(2026, 9, 4), 10, 5, 101, 102, 100, 101),
    ]
    decision = {
        "action": "LONG",
        "model_entry": 100,
        "model_stop_loss": 98,
        "model_target1": 102,
        "model_target2": 104,
    }
    assert _barrier(bars, click, decision)["first_barrier"] == (
        "AMBIGUOUS_SL_T1_SAME_5M_BAR"
    )


def test_protocol_is_candle_only_non_executing_and_per_stock():
    contract = architecture_contract()
    assert contract["candle_only"] is True
    assert contract["trading_sessions_per_stock"] == 20
    assert contract["common_session_requirement"] is False
    assert contract["session_selection"] == "PER_STOCK_LAST_20_FROM_5M_TAPE"
    assert contract["option_data_required"] is False
    assert contract["option_chain_read"] is False
    assert contract["option_premium_read"] is False
    assert contract["option_oi_read"] is False
    assert contract["iv_read"] is False
    assert contract["greeks_read"] is False
    assert contract["futures_read"] is False
    assert contract["news_read"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed"] == 0
