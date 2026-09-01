from __future__ import annotations

from .commodity_time import parse_ist_timestamp
from .trader_mind_contract import trader_mind_contract


def latest_known_as_of(records: list[dict], click_timestamp: str) -> dict[str, dict]:
    click = parse_ist_timestamp(click_timestamp)
    latest: dict[str, dict] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        series = str(record.get("series") or "").strip()
        available = record.get("available_at") or record.get("observed_at")
        if not series or not available:
            continue
        try:
            available_at = parse_ist_timestamp(available)
        except Exception:
            continue
        if available_at > click:
            continue
        observed_raw = record.get("observed_at") or available
        try:
            observed_at = parse_ist_timestamp(observed_raw)
        except Exception:
            observed_at = available_at
        row = {
            **record,
            "observed_at": observed_at.isoformat(),
            "available_at": available_at.isoformat(),
            "age_seconds": max(0.0, (click - available_at).total_seconds()),
        }
        current = latest.get(series)
        if current is None or parse_ist_timestamp(current["available_at"]) <= available_at:
            latest[series] = row
    return latest


def information_board(records: list[dict], click_timestamp: str) -> dict:
    latest = latest_known_as_of(records, click_timestamp)

    def item(series: str) -> dict:
        row = latest.get(series)
        if not row:
            return {"status": "UNAVAILABLE", "series": series}
        return {
            "status": "AVAILABLE",
            "series": series,
            "observed_at": row["observed_at"],
            "available_at": row["available_at"],
            "age_seconds": row.get("age_seconds"),
            "source": row.get("source"),
            "value": row.get("value"),
            "quality": row.get("quality"),
        }

    groups = {
        "primary_market": [item("MCX_CRUDEOILM")],
        "global_crude": [item("WTI_CRUDE"), item("BRENT_CRUDE")],
        "currency": [item("USDINR"), item("DXY")],
        "macro": [item("CRUDE_MACRO_RELEASE")],
        "news": [item("CRUDE_NEWS")],
        "options": [item("MCX_CRUDEOILM_OPTION")],
    }
    available = sum(row["status"] == "AVAILABLE" for group in groups.values() for row in group)
    total = sum(len(group) for group in groups.values())
    return {
        "mode": "CRUDE_OIL_MINI_CURRENT_MIND_INFORMATION_BOARD_V1",
        "click_timestamp": click_timestamp,
        "groups": groups,
        "availability": {"available": available, "total": total, "pct": round(available / total * 100.0, 2) if total else 0.0},
        "trader_mind": trader_mind_contract(),
        "rule": "Unavailable Crude evidence remains unavailable; absence is never converted into directional evidence.",
    }
