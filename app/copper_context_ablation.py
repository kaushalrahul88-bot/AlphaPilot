"""Research-only Copper context coverage and ablation helpers."""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from .commodity_time import parse_ist_timestamp

IST=ZoneInfo("Asia/Kolkata")


def latest_by_kind(rows):
    out={}
    for row in rows:
        cur=out.get(row.kind)
        if cur is None or row.available_at > cur.available_at:
            out[row.kind]=row
    return out


def coverage_row(store, decision_at):
    rows=store.read_available("COPPER",decision_at,("FX","POSITIONING"))
    latest=latest_by_kind(rows)
    fx=latest.get("FX")
    cot=latest.get("POSITIONING")
    return {
      "decision_at":decision_at.isoformat(),
      "fx_available":bool(fx),
      "fx_context_id":fx.context_id if fx else None,
      "fx_available_at":fx.available_at if fx else None,
      "usdinr":(fx.values or {}).get("usdinr") if fx else None,
      "positioning_available":bool(cot),
      "positioning_context_id":cot.context_id if cot else None,
      "positioning_available_at":cot.available_at if cot else None,
      "managed_money_long":(cot.values or {}).get("m_money_positions_long_all") if cot else None,
      "managed_money_short":(cot.values or {}).get("m_money_positions_short_all") if cot else None,
    }


def build_copper_context_coverage(store, experiences):
    rows=[]
    seen=set()
    for exp in experiences:
        ts=(exp.get("features") or {}).get("timestamp")
        if not ts: continue
        dt=parse_ist_timestamp(ts)
        day=dt.date().isoformat()
        if day in seen: continue
        seen.add(day)
        decision=datetime.combine(dt.date(),datetime.min.time(),tzinfo=IST).replace(hour=10)
        rows.append(coverage_row(store,decision))
    return {
      "days":len(rows),
      "fx_days":sum(r["fx_available"] for r in rows),
      "positioning_days":sum(r["positioning_available"] for r in rows),
      "both_days":sum(r["fx_available"] and r["positioning_available"] for r in rows),
      "rows":rows,
    }


def build_copper_context_coverage_for_days(store, days, decision_hour=10):
    rows=[]
    for raw in days:
        day=str(raw)[:10]
        dt=datetime.fromisoformat(day).replace(tzinfo=IST,hour=int(decision_hour))
        row=coverage_row(store,dt)
        row["test_day"]=day
        rows.append(row)
    return {
      "days":len(rows),
      "fx_days":sum(r["fx_available"] for r in rows),
      "positioning_days":sum(r["positioning_available"] for r in rows),
      "both_days":sum(r["fx_available"] and r["positioning_available"] for r in rows),
      "rows":rows,
    }
