from app.historical_context import HistoricalContext, historical_context_manifest_v1, latest_available, replay_eligible


def _ctx(i,available,value):
    return HistoricalContext(str(i),"COPPER","FX",available,available,"src","https://example.invalid","A_PRIMARY",{"v":value},"daily")


def test_context_replay_never_uses_future_availability():
    xs=[_ctx(1,"2026-08-10T10:00:00+05:30",1),_ctx(2,"2026-08-11T10:00:00+05:30",2)]
    assert replay_eligible(xs[1],"2026-08-10T15:00:00+05:30") is False
    assert latest_available(xs,"2026-08-10T15:00:00+05:30").values["v"] == 1


def test_manifest_requires_option_history_before_option_profit_claims():
    m=historical_context_manifest_v1()
    option=[x for x in m["feeds"] if x["kind"]=="OPTION_MARKET"][0]
    assert "option profitability" in option["replay_note"]
    assert m["production_rules_changed"] is False


def test_latest_context_is_point_in_time():
    xs=[_ctx(1,"2026-08-10T10:00:00+05:30",1),_ctx(2,"2026-08-11T10:00:00+05:30",2)]
    assert latest_available(xs,"2026-08-10T15:00:00+05:30").context_id=="1"
