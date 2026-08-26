from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


def parse_ist_timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = float(value)
        if raw > 1_000_000_000_000:
            raw /= 1000.0
        parsed = datetime.fromtimestamp(raw, IST)
    else:
        text = str(value).strip()
        if text.isdigit():
            raw = float(text)
            if raw > 1_000_000_000_000:
                raw /= 1000.0
            parsed = datetime.fromtimestamp(raw, IST)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)
