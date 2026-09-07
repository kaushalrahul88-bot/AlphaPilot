"""Point-in-time context enrichment V2 for the frozen four-stock direction study.

V2 intentionally keeps the V1 decision thresholds unchanged.  It expands only
the information set admitted before each click: more timestamped company,
sector and market events.  This avoids fitting thresholds to V1 outcomes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from . import fno_four_stock_context_enrichment_v1 as v1
from . import fno_candle_only_four_stock_backtest_v1 as baseline

PROTOCOL_ID = "FNO_FOUR_STOCK_CONTEXT_ENRICHMENT_V2_2026-09-07"
EVENT_ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "fno_four_stock_context_events_v2.json"

PEER_BASKETS = v1.PEER_BASKETS
MARKET_SYMBOLS = v1.MARKET_SYMBOLS
CONTEXT_SYMBOLS = v1.CONTEXT_SYMBOLS
MOMENTUM_LOOKBACK_MINUTES = v1.MOMENTUM_LOOKBACK_MINUTES
MOMENTUM_DEADBAND_PCT = v1.MOMENTUM_DEADBAND_PCT
RELATIVE_STRENGTH_DEADBAND_PCT = v1.RELATIVE_STRENGTH_DEADBAND_PCT
PROMOTE_SCORE = v1.PROMOTE_SCORE
VETO_SCORE = v1.VETO_SCORE
EVENT_MAX_AGE_DAYS = v1.EVENT_MAX_AGE_DAYS

SECTOR_BY_SYMBOL = {
    "ONGC": "ENERGY",
    "LTIM": "INFORMATION_TECHNOLOGY",
    "SBIN": "BANKING",
    "SUNPHARMA": "PHARMACEUTICALS",
}


def _load_events(path: Path = EVENT_ARCHIVE):
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", payload) if isinstance(payload, Mapping) else payload
    return list(events or [])


def _applies(event, symbol: str) -> bool:
    event_symbol = str(event.get("symbol") or "").upper()
    if event_symbol in {symbol.upper(), "MARKET"}:
        return True
    scope = str(event.get("scope") or "").upper()
    if scope.startswith("SECTOR:"):
        return scope.split(":", 1)[1] == SECTOR_BY_SYMBOL.get(symbol.upper())
    symbols = {str(item).upper() for item in (event.get("symbols") or [])}
    return symbol.upper() in symbols


def events_at(symbol: str, click, events=None):
    observed = v1._dt(click)
    output = []
    for event in events if events is not None else _load_events():
        if not _applies(event, symbol):
            continue
        effective = v1._dt(event["effective_at"])
        if effective > observed:
            continue
        age = observed - effective
        if age.total_seconds() < 0 or age > v1.timedelta(days=EVENT_MAX_AGE_DAYS):
            continue
        output.append({**event, "age_hours": round(age.total_seconds() / 3600.0, 3)})
    return sorted(output, key=lambda item: item["effective_at"])


def context_at(symbol, click, context_histories, stock_histories, events=None):
    market = v1._momentum(context_histories.get("NIFTY", []), click)
    bank = v1._momentum(context_histories.get("BANKNIFTY", []), click) if symbol == "SBIN" else None
    peers = {peer: v1._momentum(context_histories.get(peer, []), click) for peer in PEER_BASKETS[symbol]}
    peer_values = [value for value in peers.values() if value is not None]
    peer_mean = round(v1.fmean(peer_values), 6) if peer_values else None
    peer_breadth = (
        round(sum(1 if value > 0 else -1 if value < 0 else 0 for value in peer_values) / len(peer_values), 6)
        if peer_values else None
    )
    stock = v1._momentum(stock_histories.get(symbol, []), click)
    relative = round(stock - market, 6) if stock is not None and market is not None else None
    eligible_events = events_at(symbol, click, events)
    components = {
        "market": v1._sign(market),
        "sector": v1._sign(peer_mean),
        "relative_strength": v1._sign(relative, RELATIVE_STRENGTH_DEADBAND_PCT),
        "bank_index": v1._sign(bank) if symbol == "SBIN" else 0,
        "events": v1._event_score(eligible_events),
    }
    return {
        "market_60m_pct": market,
        "banknifty_60m_pct": bank,
        "stock_60m_pct": stock,
        "relative_strength_vs_nifty_pct": relative,
        "peer_60m_pct": peers,
        "peer_mean_60m_pct": peer_mean,
        "peer_breadth": peer_breadth,
        "events": eligible_events,
        "event_categories": sorted({str(event.get("category") or "UNKNOWN") for event in eligible_events}),
        "components": components,
        "context_score": sum(components.values()),
        "complete": market is not None and stock is not None and len(peer_values) >= max(2, len(PEER_BASKETS[symbol]) - 1),
    }


def enriched_decision(base_decision, technical, context):
    """Use the exact V1 decision overlay; V2 changes information, not thresholds."""
    return v1.enriched_decision(base_decision, technical, context)


def architecture_contract():
    contract = dict(v1.architecture_contract())
    contract.update({
        "version": PROTOCOL_ID,
        "event_archive": "fno_four_stock_context_events_v2.json",
        "sector_event_scopes": dict(SECTOR_BY_SYMBOL),
        "decision_thresholds_identical_to_v1": True,
        "thresholds_retuned_from_v1_results": False,
        "expanded_company_sector_market_event_archive": True,
    })
    return contract
