"""Descriptive missed-move forensics for the frozen F&O V2 replay.

This module is deliberately *not* a strategy component. It consumes already
resolved replay rows and uses outcomes only to diagnose where NO_TRADE decisions
missed meaningful subsequent movement. Nothing here may feed a live decision.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

PROTOCOL_ID = "FNO_V2_MISSED_MOVE_FORENSICS_V1_2026-09-07"
DEFAULT_THRESHOLDS_PCT = (0.5, 1.0)


def _action(row: Mapping[str, Any]) -> str:
    return str((row.get("decision") or {}).get("action") or "UNKNOWN")


def _technical_signal(row: Mapping[str, Any]) -> str:
    return str((row.get("technical") or {}).get("signal") or "UNKNOWN")


def _eod(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return ((row.get("outcome") or {}).get("eod") or {})


def _side(signal: str) -> int:
    signal = signal.upper()
    if signal in {"LONG", "STRONG_LONG", "WATCH_LONG"}:
        return 1
    if signal in {"SHORT", "STRONG_SHORT", "WATCH_SHORT"}:
        return -1
    return 0


def _sign(value: float | None) -> int:
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _bucket(signal: str) -> str:
    signal = signal.upper()
    if signal in {"LONG", "STRONG_LONG", "SHORT", "STRONG_SHORT"}:
        return "ACTIONABLE_TECHNICAL_VETOED"
    if signal in {"WATCH_LONG", "WATCH_SHORT"}:
        return "WATCH_STATE"
    return "NEUTRAL_TECHNICAL"


def audit_missed_moves(
    rows: Iterable[Mapping[str, Any]],
    thresholds_pct: tuple[float, ...] = DEFAULT_THRESHOLDS_PCT,
) -> dict[str, Any]:
    """Return a purely descriptive audit of resolved NO_TRADE rows.

    A missed move is defined by future maximum absolute excursion and therefore
    is explicitly outcome-derived. The result is suitable for research reports,
    never for point-in-time trade generation.
    """
    materialized = list(rows)
    no_trade = [row for row in materialized if _action(row) == "NO_TRADE"]

    output: dict[str, Any] = {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "descriptive_only": True,
        "strategy_gate": False,
        "outcomes_used_for_diagnosis": True,
        "observations": len(materialized),
        "no_trade_observations": len(no_trade),
        "thresholds_pct": list(thresholds_pct),
        "threshold_audits": {},
    }

    for threshold in thresholds_pct:
        misses = [
            row for row in no_trade
            if float(_eod(row).get("max_abs_excursion_pct") or 0.0) >= threshold
        ]
        by_symbol: Counter[str] = Counter()
        by_signal: Counter[str] = Counter()
        by_bucket: Counter[str] = Counter()
        directional_matches = 0
        directional_signals = 0
        eod_large = 0
        symbol_no_trade: Counter[str] = Counter(str(row.get("symbol") or "UNKNOWN") for row in no_trade)
        signal_no_trade: Counter[str] = Counter(_technical_signal(row) for row in no_trade)

        for row in misses:
            symbol = str(row.get("symbol") or "UNKNOWN")
            signal = _technical_signal(row)
            by_symbol[symbol] += 1
            by_signal[signal] += 1
            by_bucket[_bucket(signal)] += 1
            side = _side(signal)
            raw_return = _eod(row).get("raw_return_pct")
            if side:
                directional_signals += 1
                if side == _sign(float(raw_return)) if raw_return is not None else False:
                    directional_matches += 1
            if raw_return is not None and abs(float(raw_return)) >= threshold:
                eod_large += 1

        output["threshold_audits"][str(threshold)] = {
            "missed_moves": len(misses),
            "miss_rate_within_no_trade": round(len(misses) / len(no_trade), 6) if no_trade else 0.0,
            "eod_move_also_exceeded_threshold": eod_large,
            "eod_move_also_exceeded_threshold_rate": round(eod_large / len(misses), 6) if misses else 0.0,
            "directional_technical_signals": directional_signals,
            "directional_match_count": directional_matches,
            "directional_match_rate": round(directional_matches / directional_signals, 6) if directional_signals else 0.0,
            "by_symbol": {
                symbol: {
                    "missed_moves": count,
                    "no_trade_observations": symbol_no_trade[symbol],
                    "miss_rate": round(count / symbol_no_trade[symbol], 6) if symbol_no_trade[symbol] else 0.0,
                }
                for symbol, count in sorted(by_symbol.items())
            },
            "by_technical_signal": {
                signal: {
                    "missed_moves": count,
                    "no_trade_observations": signal_no_trade[signal],
                    "miss_rate": round(count / signal_no_trade[signal], 6) if signal_no_trade[signal] else 0.0,
                }
                for signal, count in sorted(by_signal.items())
            },
            "diagnostic_buckets": dict(sorted(by_bucket.items())),
        }

    return output


def research_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "descriptive_only": True,
        "may_change_live_decision": False,
        "may_retune_v1_v2_thresholds": False,
        "requires_new_out_of_sample_data_before_promotion": True,
        "purpose": "Identify failure modes and generate hypotheses without fitting the frozen benchmark.",
    }
