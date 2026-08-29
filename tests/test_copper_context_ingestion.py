from app.copper_context_ingestion import fetch_cftc_copper_positioning, fetch_fred_usdinr_daily
import app.copper_context_ingestion as mod


def test_cftc_release_is_after_observation(monkeypatch):
    payload=b'[{"report_date_as_yyyy_mm_dd":"2026-08-04T00:00:00.000","market_and_exchange_names":"COPPER-GRADE #1 - COMMODITY EXCHANGE INC.","m_money_positions_long_all":"10","m_money_positions_short_all":"20"}]'
    monkeypatch.setattr(mod,"_get",lambda url:payload)
    x=fetch_cftc_copper_positioning("2026-08-01","2026-08-10")[0]
    assert x.available_at > x.observed_at
    assert x.kind=="POSITIONING"


def test_fred_daily_is_delayed_for_replay(monkeypatch):
    payload=b"observation_date,DEXINUS\n2026-08-04,95.38\n"
    monkeypatch.setattr(mod,"_get",lambda url:payload)
    x=fetch_fred_usdinr_daily()[0]
    assert x.values["usdinr"]==95.38
    assert x.available_at > x.observed_at
