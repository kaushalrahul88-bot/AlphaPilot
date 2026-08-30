from __future__ import annotations
from .copper_point_in_time_context import latest_known_as_of
from .trader_mind_contract import trader_mind_contract

def information_board(records,click_timestamp):
    latest=latest_known_as_of(records,click_timestamp)
    def item(series):
        x=latest.get(series)
        if not x:return {"status":"UNAVAILABLE","series":series}
        return {"status":"AVAILABLE","series":series,"observed_at":x["observed_at"],
                "available_at":x["available_at"],"age_seconds":x.get("age_seconds"),
                "source":x["source"],"value":x["value"],"quality":x.get("quality")}
    groups={
      "primary_market":[item("MCX_COPPER")],
      "global_copper":[item("COMEX_HG"),item("LME_COPPER")],
      "currency":[item("USDINR"),item("DXY"),item("USDCNY")],
      "macro":[item("MACRO_RELEASE")],
      "news":[item("COPPER_NEWS")],
      "options":[item("MCX_COPPER_OPTION")],
    }
    available=sum(x["status"]=="AVAILABLE" for xs in groups.values() for x in xs)
    total=sum(len(xs) for xs in groups.values())
    return {"mode":"CURRENT_MIND_INFORMATION_BOARD_V1","click_timestamp":click_timestamp,
            "groups":groups,"availability":{"available":available,"total":total,"pct":round(available/total*100,2)},
            "trader_mind":trader_mind_contract(),
            "rule":"Unavailable evidence remains visible as unavailable; absence is never converted into bullish/bearish evidence."}
