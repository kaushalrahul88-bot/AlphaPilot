"""First-seen live tape for exact option contracts selected by frozen F&O episodes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from .fno_prospective_protocol_v1 import MAX_ACTIVE_CONTRACTS_PER_PASS, PROTOCOL_ID
from .fno_prospective_store_v1 import FnoProspectiveStore


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _chain_payload(raw: Any) -> dict:
    current = dict(raw) if isinstance(raw, Mapping) else {}
    for key in ("data", "payload"):
        child = current.get(key)
        if isinstance(child, Mapping):
            current = dict(child)
    return current


def _find_leg(chain: Mapping[str, Any], strike: float, option_type: str) -> tuple[dict, float | None]:
    payload = _chain_payload(chain)
    strikes = payload.get("strikes") or {}
    if not isinstance(strikes, Mapping):
        return {}, _number(payload.get("underlying_ltp"))
    selected = None
    for strike_key, value in strikes.items():
        try:
            key = float(strike_key)
        except (TypeError, ValueError):
            continue
        if abs(key - float(strike)) < 1e-6:
            selected = value
            break
    if not isinstance(selected, Mapping):
        return {}, _number(payload.get("underlying_ltp"))
    leg = selected.get(option_type) or {}
    return (dict(leg) if isinstance(leg, Mapping) else {}), _number(payload.get("underlying_ltp"))


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


async def _probe_direct_quote(provider, trading_symbol: str) -> dict:
    """Optional live quote probe. Failure is diagnostic and never fabricated."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{provider.BASE_URL}/v1/live-data/quote",
                headers=await provider._headers(),
                params={
                    "exchange": "NSE",
                    "segment": "FNO",
                    "trading_symbol": trading_symbol,
                },
            )
        if response.status_code != 200:
            return {"status": "UNAVAILABLE", "http_status": response.status_code}
        body = response.json()
        payload = body.get("payload", body) if isinstance(body, Mapping) else {}
        if not isinstance(payload, Mapping):
            payload = {}
        return {
            "status": "AVAILABLE",
            "http_status": 200,
            "payload": dict(payload),
        }
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "error": f"{exc.__class__.__name__}: {str(exc)[:180]}",
        }


def build_selected_observation(
    episode: Mapping[str, Any],
    chain: Mapping[str, Any],
    *,
    collected_at: datetime,
    direct_quote: Mapping[str, Any] | None = None,
) -> dict:
    """Build one first-seen exact-contract observation without inventing spread data."""
    option_type = str(episode.get("option_type") or "").upper()
    strike = _number(episode.get("strike"))
    trading_symbol = str(episode.get("trading_symbol") or "")
    if option_type not in {"CE", "PE"} or strike is None or not trading_symbol:
        raise ValueError("active F&O episode does not contain an exact selected contract")
    leg, underlying_ltp = _find_leg(chain, strike, option_type)
    greeks = leg.get("greeks") or {}
    if not isinstance(greeks, Mapping):
        greeks = {}

    quote_probe = dict(direct_quote or {})
    quote_payload = quote_probe.get("payload") or {}
    if not isinstance(quote_payload, Mapping):
        quote_payload = {}

    # Direct quote is preferred for executable fields when actually provided.
    ltp = _number(_first(quote_payload, "last_price", "ltp", "last_traded_price"))
    if ltp is None:
        ltp = _number(leg.get("ltp"))
    bid = _number(_first(quote_payload, "best_bid", "bid", "bid_price", "best_bid_price"))
    ask = _number(_first(quote_payload, "best_ask", "ask", "ask_price", "best_ask_price"))

    observed_at = collected_at.astimezone(timezone.utc)
    source_identity = {
        "episode_id": episode.get("episode_id"),
        "trading_symbol": trading_symbol,
        "observed_at": observed_at.isoformat(),
        "ltp": ltp,
        "bid": bid,
        "ask": ask,
    }
    observation_id = "fnoobs-" + hashlib.sha256(
        json.dumps(source_identity, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:28]

    record = {
        "protocol_id": PROTOCOL_ID,
        "observation_id": observation_id,
        "episode_id": str(episode["episode_id"]),
        "observed_at": observed_at.isoformat(),
        "collected_at": observed_at.isoformat(),
        "underlying_symbol": str(episode["underlying_symbol"]).upper(),
        "expiry_date": str(episode["expiry_date"]),
        "trading_symbol": trading_symbol,
        "strike": strike,
        "option_type": option_type,
        "ltp": ltp,
        "best_bid": bid,
        "best_ask": ask,
        "volume": _number(leg.get("volume")),
        "open_interest": _number(leg.get("open_interest")),
        "iv": _number(greeks.get("iv")),
        "delta": _number(greeks.get("delta")),
        "gamma": _number(greeks.get("gamma")),
        "theta": _number(greeks.get("theta")),
        "vega": _number(greeks.get("vega")),
        "underlying_ltp": underlying_ltp,
        "source": "GROWW_LIVE_OPTION_CHAIN_PLUS_OPTIONAL_DIRECT_QUOTE",
        "bid_ask_available": bid is not None and ask is not None,
        "quote_probe_status": quote_probe.get("status") or "NOT_ATTEMPTED",
        "future_outcome_present": False,
        "live_execution": False,
        "capital_committed": 0,
        "payload": {
            "chain_leg": leg,
            "direct_quote": quote_probe,
            "provider_bid_ask_not_fabricated": True,
        },
    }
    return record


async def collect_selected_contract_observations(
    provider,
    store: FnoProspectiveStore,
    *,
    now: datetime | None = None,
    limit: int = MAX_ACTIVE_CONTRACTS_PER_PASS,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    contracts = await store.active_actionable_contracts(now, limit=limit)
    if not contracts:
        return {
            "status": "COLLECTED",
            "active_contracts": 0,
            "inserted": 0,
            "failed": 0,
            "bid_ask_available": 0,
            "live_execution": False,
            "capital_committed": 0,
        }

    # One chain request per underlying+expiry; exact contract quote probes remain bounded.
    chain_cache: dict[tuple[str, str], Mapping[str, Any]] = {}
    inserted = failed = bid_ask_available = 0
    errors = []
    for episode in contracts:
        try:
            key = (episode["underlying_symbol"], episode["expiry_date"])
            chain = chain_cache.get(key)
            if chain is None:
                chain = await provider.option_chain(*key)
                chain_cache[key] = chain
            quote = await _probe_direct_quote(provider, episode["trading_symbol"])
            record = build_selected_observation(
                episode,
                chain,
                collected_at=now,
                direct_quote=quote,
            )
            result = await store.insert_observation(record)
            if result.get("status") == "INSERTED":
                inserted += 1
            if record["bid_ask_available"]:
                bid_ask_available += 1
        except Exception as exc:
            failed += 1
            errors.append({
                "episode_id": episode.get("episode_id"),
                "trading_symbol": episode.get("trading_symbol"),
                "error": f"{exc.__class__.__name__}: {str(exc)[:220]}",
            })

    return {
        "status": "COLLECTED" if failed == 0 else "PARTIAL" if inserted else "FAILED",
        "active_contracts": len(contracts),
        "inserted": inserted,
        "failed": failed,
        "bid_ask_available": bid_ask_available,
        "errors": errors,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generated": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "FNO_SELECTED_CONTRACT_TAPE_V1",
        "only_frozen_selected_contracts_observed": True,
        "first_seen_live_only": True,
        "historical_backfill": False,
        "bid_ask_fabricated": False,
        "decision_effect": "NONE",
        "options_research_only": True,
        "futures_trade_generation": False,
        "live_execution": False,
        "capital_committed": 0,
    }
