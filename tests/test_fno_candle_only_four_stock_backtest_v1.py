from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.fno_candle_only_four_stock_backtest_v1 import (
    FROZEN_STOCKS, STOCKS, architecture_contract, common_last_20_sessions,
    deterministic_clicks, _barrier,
)

IST=ZoneInfo("Asia/Kolkata")

def _bar(day,hour=9,minute=15,o=100,h=101,l=99,c=100,v=1000):
    return [datetime(day.year,day.month,day.day,hour,minute,tzinfo=IST).isoformat(),o,h,l,c,v]

def test_frozen_basket_has_four_unrelated_categories():
    assert STOCKS == ("ONGC","LTIM","SBIN","SUNPHARMA")
    assert len({category for _,category in FROZEN_STOCKS}) == 4

def test_random_clicks_are_fixed_unique_and_5m_aligned():
    day=date(2026,9,4); a=deterministic_clicks(day); b=deterministic_clicks(day)
    assert a==b and len(a)==20 and len(set(a))==20
    for stamp in a:
        local=stamp.astimezone(IST)
        assert (local.hour,local.minute) >= (9,30)
        assert (local.hour,local.minute) <= (14,0)
        assert local.minute % 5 == 0

def test_common_sessions_come_from_actual_tape_intersection():
    sessions=[]; current=date(2026,8,1)
    while len(sessions)<22:
        if current.weekday()<5:sessions.append(current)
        current+=timedelta(days=1)
    histories={s:[_bar(d) for d in sessions] for s in STOCKS}
    histories["LTIM"]=histories["LTIM"][1:]
    assert common_last_20_sessions(histories)==sessions[-20:]

def test_same_bar_sl_t1_is_not_given_favourable_ordering():
    click=datetime(2026,9,4,10,0,tzinfo=IST).astimezone(ZoneInfo("UTC"))
    bars=[_bar(date(2026,9,4),10,0,100,103,97,101),_bar(date(2026,9,4),10,5,101,102,100,101)]
    d={"action":"LONG","model_entry":100,"model_stop_loss":98,"model_target1":102,"model_target2":104}
    assert _barrier(bars,click,d)["first_barrier"]=="AMBIGUOUS_SL_T1_SAME_5M_BAR"

def test_protocol_is_candle_only_and_non_executing():
    c=architecture_contract()
    assert c["candle_only"] is True
    assert c["option_data_required"] is False
    assert c["option_chain_read"] is False
    assert c["option_premium_read"] is False
    assert c["option_oi_read"] is False
    assert c["iv_read"] is False
    assert c["greeks_read"] is False
    assert c["futures_read"] is False
    assert c["news_read"] is False
    assert c["live_execution"] is False
    assert c["capital_committed"]==0
