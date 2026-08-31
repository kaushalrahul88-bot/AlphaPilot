from __future__ import annotations
from datetime import timedelta
from app.commodity_time import parse_ist_timestamp

def align_option_snapshot_with_candles(
    option_snapshot: dict,
    candle_rows: list,
    interval_minutes: int = 5,
    max_candle_age_minutes: int = 15,
) -> dict:
    """Build a leak-free point-in-time context around a stored option snapshot.

    Candle rows may be fetched later from a historical provider, but only bars
    fully completed by the option snapshot's observed_at timestamp are eligible.
    """
    if not isinstance(option_snapshot, dict):
        raise ValueError("option_snapshot must be a dict")
    observed_raw=option_snapshot.get("observed_at")
    if not observed_raw:
        raise ValueError("option snapshot requires observed_at")
    observed=parse_ist_timestamp(observed_raw)
    interval=max(1,int(interval_minutes))
    duration=timedelta(minutes=interval)

    completed=[]
    for row in candle_rows or []:
        if not isinstance(row,(list,tuple)) or len(row)<5:
            continue
        try:
            start=parse_ist_timestamp(row[0])
        except Exception:
            continue
        if start+duration<=observed:
            completed.append(list(row))
    completed.sort(key=lambda r: parse_ist_timestamp(r[0]))

    latest=completed[-1] if completed else None
    latest_completed_at=(parse_ist_timestamp(latest[0])+duration) if latest else None
    age_minutes=((observed-latest_completed_at).total_seconds()/60.0) if latest_completed_at else None
    candle_fresh=age_minutes is not None and 0<=age_minutes<=max(0,int(max_candle_age_minutes))

    underlying=str(option_snapshot.get("underlying_symbol") or "").upper().strip()
    return {
        "underlying_symbol":underlying,
        "option_expiry":option_snapshot.get("expiry_date"),
        "option_observed_at":observed.isoformat(),
        "candle_interval_minutes":interval,
        "completed_candle_count":len(completed),
        "latest_completed_candle_at":latest_completed_at.isoformat() if latest_completed_at else None,
        "candle_age_minutes":round(age_minutes,2) if age_minutes is not None else None,
        "candle_fresh":candle_fresh,
        "point_in_time_safe":all(
            parse_ist_timestamp(r[0])+duration<=observed for r in completed
        ),
        "join_key":{
            "underlying_symbol":underlying,
            "as_of":observed.isoformat(),
        },
        "candles":completed,
        "option_payload":option_snapshot.get("payload"),
    }
