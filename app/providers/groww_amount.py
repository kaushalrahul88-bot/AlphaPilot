import csv
import io
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .groww_memory_safe import MemorySafeGrowwProvider


class AmountAwareGrowwProvider(MemorySafeGrowwProvider):
    """Adds live one-lot capital requirement to confirmed option setups.

    Lot sizes are sourced from Groww's official instrument master CSV and cached
    process-wide so scans do not repeatedly download the file.
    """

    INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
    _lot_cache = {}
    _lot_cache_loaded_at = 0.0
    _lot_cache_ttl = 6 * 60 * 60
    _freshness_limits_minutes = {"5m": 15, "15m": 35, "1h": 100}

    @classmethod
    async def _ensure_lot_cache(cls):
        now = time.time()
        if cls._lot_cache and now - cls._lot_cache_loaded_at < cls._lot_cache_ttl:
            return

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(cls.INSTRUMENT_CSV_URL)
            response.raise_for_status()

        cache = {}
        reader = csv.DictReader(io.StringIO(response.text))
        for row in reader:
            if str(row.get("exchange", "")).upper() != "NSE":
                continue
            if str(row.get("segment", "")).upper() != "FNO":
                continue
            option_type = str(row.get("instrument_type", "")).upper()
            if option_type not in ("CE", "PE"):
                continue
            underlying = str(row.get("underlying_symbol", "")).upper().strip()
            expiry = str(row.get("expiry_date", "")).strip()
            try:
                strike = round(float(row.get("strike_price") or 0), 4)
                lot_size = int(float(row.get("lot_size") or 0))
            except (TypeError, ValueError):
                continue
            if underlying and expiry and strike > 0 and lot_size > 0:
                cache[(underlying, expiry, strike, option_type)] = lot_size

        if cache:
            cls._lot_cache = cache
            cls._lot_cache_loaded_at = now

    @classmethod
    async def _lot_size_for_option(cls, option):
        if not isinstance(option, dict):
            return None
        try:
            await cls._ensure_lot_cache()
        except Exception:
            return None

        underlying = str(option.get("underlying") or "").upper().strip()
        expiry = str(option.get("expiry") or "").strip()
        option_type = str(option.get("option_type") or "").upper().strip()
        try:
            strike = round(float(option.get("strike") or 0), 4)
        except (TypeError, ValueError):
            return None
        return cls._lot_cache.get((underlying, expiry, strike, option_type))

    @staticmethod
    def _option_risk_reward(option):
        if not isinstance(option, dict):
            return None
        try:
            entry = float(option.get("option_entry") or option.get("premium") or 0)
            stop = float(option.get("option_stop_loss") or 0)
            target = float(option.get("option_target1") or 0)
        except (TypeError, ValueError):
            return None
        risk = entry - stop
        reward = target - entry
        if entry <= 0 or stop <= 0 or target <= 0 or risk <= 0 or reward <= 0:
            return None
        return reward / risk

    @classmethod
    def _apply_market_data_freshness_gate(cls, result):
        if not isinstance(result, dict):
            return
        session = result.get("market_session") or {}
        if session.get("execution_allowed") is not True:
            return

        technical = result.get("technical") or {}
        frames = technical.get("timeframes") or {}
        blockers = result.setdefault("execution_blockers", [])
        now = datetime.now(ZoneInfo("Asia/Kolkata"))

        for timeframe, limit in cls._freshness_limits_minutes.items():
            row = frames.get(timeframe)
            if not isinstance(row, dict):
                continue
            raw = row.get("latest_candle_at")
            if not raw:
                blockers.append(f"MARKET_DATA_STALE: {timeframe} latest candle timestamp is unavailable.")
                continue
            try:
                candle_time = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if candle_time.tzinfo is None:
                    candle_time = candle_time.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                else:
                    candle_time = candle_time.astimezone(ZoneInfo("Asia/Kolkata"))
                age_minutes = max(0.0, (now - candle_time).total_seconds() / 60.0)
            except Exception:
                blockers.append(f"MARKET_DATA_STALE: {timeframe} candle timestamp could not be validated.")
                continue
            if age_minutes > limit:
                blockers.append(
                    f"MARKET_DATA_STALE: latest {timeframe} candle is {age_minutes:.0f} minutes old; live limit is {limit} minutes."
                )

        result["execution_blockers"] = list(dict.fromkeys(blockers))

    @classmethod
    def _apply_option_quality_gate(cls, result, option, min_rr):
        if not isinstance(result, dict) or not isinstance(option, dict):
            return

        blockers = result.setdefault("execution_blockers", [])
        option_rr = cls._option_risk_reward(option)
        option["option_risk_reward"] = round(option_rr, 2) if option_rr is not None else None

        if option_rr is None:
            blockers.append("OPTION_RR_INVALID: option entry/stop/target do not form a valid positive-risk trade plan.")
        elif option_rr < float(min_rr):
            blockers.append(
                f"OPTION_RR_LOW: projected option R:R {option_rr:.2f}:1 is below the required {float(min_rr):.2f}:1."
            )

        volume = option.get("volume")
        if isinstance(volume, (int, float)) and volume <= 0:
            blockers.append("OPTION_LIQUIDITY: recommended contract has no reported traded volume.")

        iv = option.get("iv")
        if isinstance(iv, (int, float)) and (iv <= 0 or iv > 200):
            blockers.append("OPTION_IV_INVALID: reported implied volatility is outside a usable sanity range.")

        amount = option.get("amount_required_1_lot")
        if not isinstance(amount, (int, float)) or amount <= 0:
            blockers.append("CAPITAL_UNKNOWN: one-lot capital requirement could not be validated from the current Groww instrument master.")

        result["execution_blockers"] = list(dict.fromkeys(blockers))
        result["execution_ready"] = not result["execution_blockers"]
        if not result["execution_ready"] and result.get("status") == "SETUP":
            result["status"] = "NO_TRADE"

    @classmethod
    def _execution_quality(cls, result, option, min_rr):
        session = result.get("market_session") or {}
        blockers = [str(x) for x in (result.get("execution_blockers") or [])]
        prefixes = {b.split(":", 1)[0] for b in blockers if ":" in b}
        option = option if isinstance(option, dict) else {}
        option_rr = cls._option_risk_reward(option)
        volume = option.get("volume")
        iv = option.get("iv")
        amount = option.get("amount_required_1_lot")
        oi = option.get("open_interest")

        checks = {
            "market_session": {
                "pass": session.get("execution_allowed") is True,
                "value": session.get("phase") or session.get("status") or "UNKNOWN",
            },
            "market_data_fresh": {
                "pass": "MARKET_DATA_STALE" not in prefixes,
                "limits_minutes": dict(cls._freshness_limits_minutes),
            },
            "underlying_plan": {
                "pass": "DATA_INCOMPLETE" not in prefixes,
            },
            "option_plan": {
                "pass": bool(option.get("option_plan_ready")) and "OPTION_PLAN_INCOMPLETE" not in prefixes,
            },
            "option_risk_reward": {
                "pass": option_rr is not None and option_rr >= float(min_rr),
                "value": round(option_rr, 2) if option_rr is not None else None,
                "minimum": round(float(min_rr), 2),
            },
            "open_interest": {
                "pass": isinstance(oi, (int, float)) and oi > 0,
                "value": oi,
            },
            "volume": {
                "pass": isinstance(volume, (int, float)) and volume > 0,
                "value": volume,
            },
            "iv_sanity": {
                "pass": not isinstance(iv, (int, float)) or 0 < iv <= 200,
                "value": iv,
            },
            "one_lot_capital": {
                "pass": isinstance(amount, (int, float)) and amount > 0,
                "value": amount,
            },
        }
        passed = sum(1 for check in checks.values() if check.get("pass") is True)
        return {
            "ready": result.get("execution_ready") is True,
            "checks_passed": passed,
            "checks_total": len(checks),
            "checks": checks,
            "blockers": blockers,
        }

    async def fno_confirm(self, *args, **kwargs):
        result = await super().fno_confirm(*args, **kwargs)
        option = result.get("recommended_option") if isinstance(result, dict) else None

        min_rr = kwargs.get("min_rr")
        if min_rr is None and len(args) >= 3:
            min_rr = args[2]
        try:
            min_rr = float(min_rr)
        except (TypeError, ValueError):
            min_rr = 1.5

        if not isinstance(option, dict):
            if isinstance(result, dict):
                result["execution_quality"] = self._execution_quality(result, None, min_rr)
            return result

        lot_size = await self._lot_size_for_option(option)
        entry = option.get("option_entry")
        if not isinstance(entry, (int, float)) or entry <= 0:
            entry = option.get("premium")

        option["lot_size"] = lot_size
        option["amount_required_1_lot"] = (
            round(float(entry) * lot_size, 2)
            if isinstance(entry, (int, float)) and entry > 0 and isinstance(lot_size, int) and lot_size > 0
            else None
        )
        option["capital_basis"] = "Option entry premium × current Groww F&O lot size"

        if option.get("amount_required_1_lot") is not None:
            base_label = str(option.get("contract_label") or "").split(" · 1 lot:")[0]
            option["contract_label"] = (
                f"{base_label} · 1 lot: {lot_size} qty · ₹{option['amount_required_1_lot']:,.2f} required"
            )

        self._apply_market_data_freshness_gate(result)
        self._apply_option_quality_gate(result, option, min_rr)
        result["execution_quality"] = self._execution_quality(result, option, min_rr)
        return result
