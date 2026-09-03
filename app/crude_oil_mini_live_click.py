from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_current_mind import crude_oil_mini_current_mind_click
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_direction_brain_v2_integrated import evaluate_integrated_direction_v2_shadow
from .crude_oil_mini_direction_memory import HORIZONS, make_direction_case
from .crude_oil_mini_experience_memory import build_experiences, memory_evidence
from .crude_oil_mini_market_perception import (
    bar_visible_at,
    causal_profiles,
    clean_ohlcv,
    latest_visible_index,
    market_regime_features,
    precompute_perception,
    price_evidence,
)
from .current_mind_crude_oil_mini_replay import _dominant_direction, _geometry

IST = ZoneInfo("Asia/Kolkata")
MODE = "CRUDE_OIL_MINI_CURRENT_MIND_LIVE_SHADOW_V1"


def _build_direction_memory(rows: list[list], features: list[dict]) -> list[dict]:
    complete_days = {
        item["date"]
        for item in _complete_sessions(rows)
        if item.get("complete_for_20_click_research")
    }
    by_day: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        day = parse_ist_timestamp(row[0]).astimezone(IST).date().isoformat()
        if day in complete_days:
            by_day[day].append(index)

    cases: list[dict] = []
    for day in sorted(by_day):
        indices = by_day[day]
        if len(indices) <= 48:
            continue
        closes_by_visible_at = {
            bar_visible_at(rows[index]).isoformat(): float(rows[index][4])
            for index in indices
        }
        for index in indices[24:-24:3]:
            click = bar_visible_at(rows[index])
            base = float(rows[index][4])
            if base <= 0:
                continue
            future_returns = {}
            for minutes in HORIZONS:
                close = closes_by_visible_at.get((click + timedelta(minutes=minutes)).isoformat())
                if close is None:
                    break
                future_returns[str(minutes)] = (close / base - 1.0) * 100.0
            if len(future_returns) != len(HORIZONS):
                continue
            cases.append(
                make_direction_case(
                    snapshot=features[index],
                    click_timestamp=click.isoformat(),
                    available_at=(click + timedelta(minutes=max(HORIZONS))).isoformat(),
                    future_returns_pct=future_returns,
                )
            )
    return cases


def _context_records_from_probe(probe: dict, click_timestamp: str) -> list[dict]:
    click = parse_ist_timestamp(click_timestamp).astimezone(IST)
    records: list[dict] = []
    for series in ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY"):
        feed = (probe.get("feeds") or {}).get(series) or {}
        if feed.get("status") != "AVAILABLE":
            continue
        visible = []
        for row in feed.get("data") or []:
            try:
                available = parse_ist_timestamp(row["available_at"]).astimezone(IST)
                close = float(row["close"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if available <= click and close > 0:
                visible.append((available, row, close))
        if not visible:
            continue
        visible.sort(key=lambda item: item[0])
        available, latest, close = visible[-1]
        records.append({
            "series": series,
            "observed_at": latest["bar_start"],
            "available_at": available.isoformat(),
            "source": "Yahoo Finance public chart",
            "quality": "E_DISCOVERY",
            "value": {"close": close},
        })
    return records


def evaluate_live_current_mind(
    candles,
    *,
    contract: dict,
    global_context_probe: dict,
    click_at: datetime | str | None = None,
) -> dict:
    click = parse_ist_timestamp(click_at or datetime.now(IST)).astimezone(IST)
    rows, features = precompute_perception(clean_ohlcv(candles))
    if not rows:
        raise RuntimeError("No CRUDEOILM candles available")

    visible_index = latest_visible_index(rows, click)
    if visible_index is None:
        raise RuntimeError("No completed CRUDEOILM bar is visible at the live click")
    snapshot = features[visible_index]
    latest_visible = bar_visible_at(rows[visible_index])
    if latest_visible > click:
        raise RuntimeError("Future/open candle leaked into live click")

    profiles = causal_profiles(rows, features)
    profile = profiles.get(click.date().isoformat()) or {}
    complete_days = {
        item["date"]
        for item in _complete_sessions(rows)
        if item.get("complete_for_20_click_research")
    }
    experiences = build_experiences(rows, features, complete_days, sample_every_bars=3)
    evidence_items = price_evidence(snapshot, profile)
    evidence_items.append(memory_evidence(experiences, snapshot, click.isoformat()))
    direction = _dominant_direction(evidence_items)

    same_session_rows = [
        row for row in rows
        if parse_ist_timestamp(row[0]).astimezone(IST).date() == click.date()
        and bar_visible_at(row) <= click
    ]
    market = market_regime_features(snapshot, profile)
    market.update(_geometry(same_session_rows, direction) if direction else {})

    reference_contract = str(contract.get("trading_symbol") or "CRUDEOILM_CURRENT_FUTURE")
    current_context = [{
        "series": "MCX_CRUDEOILM",
        "observed_at": snapshot["timestamp"],
        "available_at": latest_visible.isoformat(),
        "source": reference_contract,
        "value": {"price": snapshot.get("price")},
        "quality": "OBSERVED",
    }]
    journal = crude_oil_mini_current_mind_click(
        click_timestamp=click.isoformat(),
        context_records=current_context,
        market_features=market,
        evidence_items=evidence_items,
        memory_cases=[],
    )

    direction_memory = _build_direction_memory(rows, features)
    cross_market_records = _context_records_from_probe(global_context_probe, click.isoformat())
    integrated = evaluate_integrated_direction_v2_shadow(
        click_timestamp=click.isoformat(),
        snapshot=snapshot,
        profile=profile,
        mini_candles=rows,
        global_context_probe=global_context_probe,
        context_records=cross_market_records,
        event_records=[],
        direction_memory_cases=direction_memory,
    )

    decision = journal.get("decision") or {}
    action = str(decision.get("action") or "NO_TRADE")
    return {
        "mode": MODE,
        "generated_at": datetime.now(IST).isoformat(),
        "click_at": click.isoformat(),
        "point_in_time": True,
        "product": "CRUDE_OIL_MINI",
        "symbol": "CRUDEOILM",
        "reference_contract": reference_contract,
        "latest_completed_bar_available_at": latest_visible.isoformat(),
        "current_mind": {
            "action": action,
            "direction": decision.get("direction"),
            "reason": decision.get("reason"),
            "playbook": decision.get("playbook"),
            "thesis": decision.get("thesis"),
            "entry_trigger": decision.get("entry_trigger"),
            "invalidation": decision.get("invalidation"),
            "target_or_exit_logic": decision.get("target_or_exit_logic"),
            "evidence_quality": decision.get("evidence_quality"),
            "risk_review": decision.get("risk_review"),
            "entry_price": market.get("entry_price"),
            "stop_price": market.get("stop_price"),
            "target_price": market.get("target_price"),
        },
        "integrated_v2_shadow": {
            "direction": integrated.get("direction"),
            "confidence": integrated.get("direction_confidence"),
            "thesis_state": integrated.get("thesis_state"),
            "supporting_families": integrated.get("supporting_families") or [],
            "opposing_families": integrated.get("opposing_families") or [],
            "families": integrated.get("families") or {},
            "decision_effect": "NONE",
        },
        "data": {
            "candles": len(rows),
            "historical_direction_memory_cases": len(direction_memory),
            "global_context": {
                name: ((global_context_probe.get("feeds") or {}).get(name) or {}).get("status", "UNAVAILABLE")
                for name in ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY")
            },
            "events": "NOT_WIRED_IN_LIVE_SHADOW_V1",
            "futures_oi": "USE_IF_PRESENT_OTHERWISE_NOT_RECONSTRUCTED",
        },
        "execution": {
            "paper_signal_only": True,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "option_expression": None,
            "capital_committed": 0,
        },
        "journal": journal,
    }
