from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Official MCX 2026 trading-holiday schedule (morning/evening session status).
# Morning session: 09:00-17:00. Evening session for metals/energy follows thereafter.
MCX_2026_TRADING_HOLIDAYS = {
    date(2026,1,1): ("NEW YEAR DAY", True, False),
    date(2026,1,26): ("REPUBLIC DAY", False, False),
    date(2026,3,3): ("HOLI", False, True),
    date(2026,3,26): ("SHRI RAM NAVMI", False, True),
    date(2026,3,31): ("SHRI MAHAVIR JAYANTI", False, True),
    date(2026,4,3): ("GOOD FRIDAY", False, False),
    date(2026,4,14): ("DR. BABA SAHEB AMBEDKAR JAYANTI", False, True),
    date(2026,5,1): ("MAHARASHTRA DAY", False, True),
    date(2026,5,28): ("BAKRI ID", False, True),
    date(2026,6,26): ("MOHARRAM", False, True),
    date(2026,9,14): ("GANESH CHATURTHI", False, True),
    date(2026,10,2): ("MAHATMA GANDHI JAYANTI", False, False),
    date(2026,10,20): ("DASSERA", False, True),
    date(2026,11,10): ("DIWALI-BALIPRATIPADA", False, True),
    date(2026,11,24): ("GURU NANAK JAYANTI", False, True),
    date(2026,12,25): ("CHRISTMAS", False, False),
}


def _metal_evening_close(day: date) -> time:
    # MCX internationally referenceable non-agri products close at 23:30
    # during the spring/summer DST period and 23:55 after US DST ends.
    # For the August 2026 audit this deterministically resolves to 23:30.
    return time(23,55) if day.month in {1,2,3,11,12} else time(23,30)


def mcx_metal_day_schedule(day: date) -> dict:
    if day.weekday() >= 5:
        return {
            "date": day.isoformat(),
            "calendar_class": "WEEKEND",
            "holiday_name": None,
            "morning_open": False,
            "evening_open": False,
            "expected_open": False,
            "session_windows": [],
            "expected_5m_bars": 0,
        }

    holiday = MCX_2026_TRADING_HOLIDAYS.get(day)
    holiday_name = None
    morning_open = True
    evening_open = True
    if holiday:
        holiday_name, morning_open, evening_open = holiday

    windows = []
    if morning_open:
        windows.append(("09:00", "17:00"))
    if evening_open:
        windows.append(("17:00", _metal_evening_close(day).strftime("%H:%M")))

    expected = 0
    for start_text, end_text in windows:
        sh, sm = map(int, start_text.split(":"))
        eh, em = map(int, end_text.split(":"))
        minutes = (eh * 60 + em) - (sh * 60 + sm)
        expected += max(0, minutes // 5)

    return {
        "date": day.isoformat(),
        "calendar_class": "TRADING_HOLIDAY" if holiday else "REGULAR_WEEKDAY",
        "holiday_name": holiday_name,
        "morning_open": morning_open,
        "evening_open": evening_open,
        "expected_open": bool(morning_open or evening_open),
        "session_windows": [{"start":a,"end":b} for a,b in windows],
        "expected_5m_bars": expected,
    }


def mcx_metal_session_status(now: datetime | None = None) -> dict:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    schedule = mcx_metal_day_schedule(now.date())
    is_open = False
    for window in schedule["session_windows"]:
        start = time.fromisoformat(window["start"])
        end = time.fromisoformat(window["end"])
        if start <= now.time() <= end:
            is_open = True
            break
    return {
        "status": "OPEN" if is_open else "CLOSED",
        "is_open": is_open,
        "checked_at": now.isoformat(),
        "calendar": schedule,
    }
