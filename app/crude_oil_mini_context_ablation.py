from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from statistics import mean

from .commodity_time import parse_ist_timestamp


def _action_direction(action: str) -> int:
    return 1 if action == "BUY_CE" else -1 if action == "BUY_PE" else 0


def _series_index(payload: dict, series: str):
    rows = list((((payload.get("feeds") or {}).get(series) or {}).get("rows") or []))
    available = [parse_ist_timestamp(row["available_at"]) for row in rows]
    return rows, available


def _latest_direction(index, click_at) -> tuple[int, dict | None, dict | None]:
    rows, available = index
    click = parse_ist_timestamp(click_at)
    pos = bisect_right(available, click) - 1
    if pos < 1:
        return 0, None, None
    current, previous = rows[pos], rows[pos - 1]
    a, b = float(current["close"]), float(previous["close"])
    direction = 1 if a > b else -1 if a < b else 0
    return direction, previous, current


def _context_state(indexes: dict, click_at: str) -> dict:
    wti, _, wti_now = _latest_direction(indexes["WTI_CRUDE"], click_at)
    brent, _, brent_now = _latest_direction(indexes["BRENT_CRUDE"], click_at)
    fx, _, fx_now = _latest_direction(indexes["USDINR"], click_at)
    dxy, _, dxy_now = _latest_direction(indexes["DXY"], click_at)
    global_crude = wti if wti != 0 and wti == brent else 0
    return {
        "wti_direction": wti,
        "brent_direction": brent,
        "global_crude_direction": global_crude,
        "usdinr_direction": fx,
        "dxy_direction": dxy,
        "latest_available_at": {
            "WTI_CRUDE": wti_now.get("available_at") if wti_now else None,
            "BRENT_CRUDE": brent_now.get("available_at") if brent_now else None,
            "USDINR": fx_now.get("available_at") if fx_now else None,
            "DXY": dxy_now.get("available_at") if dxy_now else None,
        },
    }


def _variant_action(base_action: str, state: dict, variant: str) -> str:
    direction = _action_direction(base_action)
    if not direction:
        return "WAIT"
    if variant == "A":
        return base_action
    if state["global_crude_direction"] != direction:
        return "WAIT"
    if variant == "B":
        return base_action
    if state["usdinr_direction"] != direction:
        return "WAIT"
    if variant == "C":
        return base_action
    raise ValueError(f"Unknown context-ablation variant: {variant}")


def _aggregate(rows: list[dict], action_key: str) -> dict:
    trades = [row for row in rows if row[action_key] in {"BUY_CE", "BUY_PE"}]
    resolved = [row for row in trades if (row.get("outcome") or {}).get("result") in {"TARGET", "STOP"}]
    rs = [float((row.get("outcome") or {}).get("realized_r") or 0.0) for row in resolved]
    out = {
        "clicks": len(rows),
        "trades": len(trades),
        "waits": len(rows) - len(trades),
        "resolved_trades": len(resolved),
        "targets": sum((row.get("outcome") or {}).get("result") == "TARGET" for row in trades),
        "stops": sum((row.get("outcome") or {}).get("result") == "STOP" for row in trades),
        "expectancy_r_resolved": round(mean(rs), 4) if rs else None,
    }
    for minutes in (15, 30, 60):
        signed = []
        for row in trades:
            raw = (row.get("future_returns_pct") or {}).get(str(minutes))
            if raw is None:
                continue
            value = float(raw)
            signed.append(value if row[action_key] == "BUY_CE" else -value)
        out[f"direction_{minutes}m"] = {
            "observations": len(signed),
            "alignment_pct": round(sum(value > 0 for value in signed) / len(signed) * 100.0, 2) if signed else None,
            "avg_signed_return_pct": round(mean(signed), 4) if signed else None,
        }
    return out


def _chronological_windows(rows: list[dict], action_key: str) -> list[dict]:
    sessions = sorted({str(row.get("session")) for row in rows})
    if not sessions:
        return []
    n = len(sessions)
    cuts = [0, (n + 2) // 3, (2 * n + 2) // 3, n]
    windows = []
    for i in range(3):
        days = set(sessions[cuts[i]:cuts[i + 1]])
        subset = [row for row in rows if str(row.get("session")) in days]
        windows.append({
            "window": i + 1,
            "first_session": min(days) if days else None,
            "last_session": max(days) if days else None,
            "summary": _aggregate(subset, action_key),
        })
    return windows


def evaluate_crude_context_ablation(replay: dict, context_payload: dict) -> dict:
    """Exploratory Copper-style context ablation on the frozen Crude decisions.

    Rules are economic-mechanism based and fixed in code before this function reads
    any outcome.  Because the baseline June-August outcomes were already inspected
    before this experiment was designed, this report is exploratory and cannot by
    itself promote a context rule.
    """
    decisions = list(replay.get("decisions") or [])
    indexes = {series: _series_index(context_payload, series) for series in ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY")}
    rows = []
    coverage = Counter()
    for base in decisions:
        state = _context_state(indexes, base["click_timestamp"])
        action = str(base.get("action") or "WAIT")
        row = dict(base)
        row["context_state"] = state
        row["variant_A_action"] = _variant_action(action, state, "A")
        row["variant_B_action"] = _variant_action(action, state, "B")
        row["variant_C_action"] = _variant_action(action, state, "C")
        row["B_changed"] = row["variant_B_action"] != row["variant_A_action"]
        row["C_changed"] = row["variant_C_action"] != row["variant_B_action"]
        if state["global_crude_direction"]:
            coverage["global_crude_direction_known"] += 1
        if state["usdinr_direction"]:
            coverage["usdinr_direction_known"] += 1
        if state["dxy_direction"]:
            coverage["dxy_direction_known"] += 1
        rows.append(row)

    variants = {}
    for variant in ("A", "B", "C"):
        key = f"variant_{variant}_action"
        variants[variant] = {
            "summary": _aggregate(rows, key),
            "chronological_windows": _chronological_windows(rows, key),
        }

    return {
        "mode": "CRUDE_OIL_MINI_CONTEXT_ABLATION_DISCOVERY_V1",
        "research_only": True,
        "analysis_class": "EXPLORATORY_POST_BASELINE",
        "context_source_grade": context_payload.get("source_grade"),
        "promotion_allowed": False,
        "requires_authorized_or_independent_validation": True,
        "decision_path_changed": False,
        "baseline_decisions_mutated": False,
        "clicks": len(rows),
        "coverage": {
            "global_crude_direction_known_clicks": coverage["global_crude_direction_known"],
            "usdinr_direction_known_clicks": coverage["usdinr_direction_known"],
            "dxy_direction_known_clicks": coverage["dxy_direction_known"],
        },
        "preregistered_variants": {
            "A": "Frozen existing CRUDEOILM no-news actions; no global context filter.",
            "B": "A trades survive only when latest completed-hour WTI and Brent both move in the trade direction. WTI+Brent are one correlated GLOBAL_CRUDE context state, not two votes.",
            "C": "B trades survive only when latest completed-hour USD/INR change also supports the INR-denominated MCX trade direction.",
            "DXY": "Measured descriptively only; no directional DXY gate is assumed in V1.",
        },
        "changed_clicks": {
            "A_to_B": sum(row["B_changed"] for row in rows),
            "B_to_C": sum(row["C_changed"] for row in rows),
        },
        "variants": variants,
        "rows": rows,
        "guardrails": [
            "Context can only turn an existing trade into WAIT; it cannot create or reverse a trade.",
            "Only context bars whose available_at <= click_timestamp are visible.",
            "Hourly context becomes visible only after the hour completes.",
            "WTI and Brent are treated as one correlated global-crude state.",
            "No return-magnitude threshold was fit to Crude outcomes.",
            "DXY has no assumed directional rule in V1.",
            "Discovery-grade source results cannot be promoted without authorized or independent validation.",
            "June-August baseline outcomes were previously seen, so this is not an untouched holdout.",
        ],
    }
