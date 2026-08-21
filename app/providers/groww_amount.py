import csv
import io
import time

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

    async def fno_confirm(self, *args, **kwargs):
        result = await super().fno_confirm(*args, **kwargs)
        option = result.get("recommended_option") if isinstance(result, dict) else None
        if not isinstance(option, dict):
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

        return result
