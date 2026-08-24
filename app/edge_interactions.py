from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from statistics import mean

from .edge_discovery import run_edge_discovery


def _num(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bucket(value, cuts, labels):
    v = _num(value)
    if v is None:
        return "UNKNOWN"
    if v < cuts[0]:
        return labels[0]
    if v < cuts[1]:
        return labels[1]
    return labels[2]


def _categories(row: dict) -> dict[str, str]:
    brain = row.get("market_brain") or {}
    return {
        "option_type": str(row.get("option_type") or "UNKNOWN"),
        "market_regime": str(brain.get("final_regime") or "UNKNOWN"),
        "premium_atr_pct": _bucket(row.get("premium_atr_pct"), (2.0, 5.0), ("LOW", "NORMAL", "HIGH")),
        "premium_volume_ratio": _bucket(row.get("premium_volume_ratio"), (0.8, 1.3), ("WEAK", "NORMAL", "EXPANDING")),
        "premium_vwap_gap_pct": _bucket(row.get("premium_vwap_gap_pct"), (-1.0, 1.0), ("BELOW", "NEAR", "ABOVE")),
        "premium_return_3bar_pct": _bucket(row.get("premium_return_3bar_pct"), (-1.0, 3.0), ("WEAK", "NEUTRAL", "STRONG")),
        "ce_pe_relative_edge_pct": _bucket(row.get("ce_pe_relative_edge_pct"), (-2.0, 2.0), ("LAGGING", "NEUTRAL", "LEADING")),
    }


def _baseline(observations: list[dict]) -> dict:
    n = len(observations)
    if not n:
        return {"observations": 0, "hit_0_5r_pct": 0.0, "hit_1_0r_pct": 0.0, "hit_1_5r_pct": 0.0, "avg_mfe_r": 0.0, "avg_mae_r": 0.0}
    labels = [x.get("labels") or {} for x in observations]
    return {
        "observations": n,
        "hit_0_5r_pct": round(sum(bool(x.get("hit_0_5r_before_stop")) for x in labels) / n * 100.0, 1),
        "hit_1_0r_pct": round(sum(bool(x.get("hit_1_0r_before_stop")) for x in labels) / n * 100.0, 1),
        "hit_1_5r_pct": round(sum(bool(x.get("hit_1_5r_before_stop")) for x in labels) / n * 100.0, 1),
        "avg_mfe_r": round(mean(_num(x.get("mfe_r"), 0.0) or 0.0 for x in labels), 3),
        "avg_mae_r": round(mean(_num(x.get("mae_r"), 0.0) or 0.0 for x in labels), 3),
    }


def _summarize(sample: list[dict], baseline: dict, feature_names: tuple[str, ...], values: tuple[str, ...]) -> dict:
    n = len(sample)
    labels = [x.get("labels") or {} for x in sample]
    hit05 = sum(bool(x.get("hit_0_5r_before_stop")) for x in labels) / n * 100.0 if n else 0.0
    hit10 = sum(bool(x.get("hit_1_0r_before_stop")) for x in labels) / n * 100.0 if n else 0.0
    hit15 = sum(bool(x.get("hit_1_5r_before_stop")) for x in labels) / n * 100.0 if n else 0.0
    mfe = mean(_num(x.get("mfe_r"), 0.0) or 0.0 for x in labels) if n else 0.0
    mae = mean(_num(x.get("mae_r"), 0.0) or 0.0 for x in labels) if n else 0.0
    base10 = float(baseline.get("hit_1_0r_pct") or 0.0)
    lift_pp = hit10 - base10
    ratio = hit10 / base10 if base10 > 0 else None
    mfe_mae = mfe / mae if mae > 0 else None
    if n >= 50 and lift_pp >= 5.0 and (ratio or 0.0) >= 1.25:
        flag = "PROMISING"
    elif n >= 30 and lift_pp > 0:
        flag = "WATCH"
    else:
        flag = "WEAK"
    return {
        "features": list(feature_names),
        "values": list(values),
        "label": " × ".join(f"{name}={value}" for name, value in zip(feature_names, values)),
        "observations": n,
        "hit_0_5r_pct": round(hit05, 1),
        "hit_1_0r_pct": round(hit10, 1),
        "hit_1_5r_pct": round(hit15, 1),
        "lift_1_0r_pp": round(lift_pp, 1),
        "relative_1_0r_x": round(ratio, 2) if ratio is not None else None,
        "avg_mfe_r": round(mfe, 3),
        "avg_mae_r": round(mae, 3),
        "mfe_mae_ratio": round(mfe_mae, 2) if mfe_mae is not None else None,
        "stability_flag": flag,
    }


def _interaction_rows(observations: list[dict], baseline: dict, min_observations: int, order: int) -> list[dict]:
    features = (
        "option_type",
        "market_regime",
        "premium_atr_pct",
        "premium_volume_ratio",
        "premium_vwap_gap_pct",
        "premium_return_3bar_pct",
        "ce_pe_relative_edge_pct",
    )
    categorized = [(row, _categories(row)) for row in observations]
    output = []
    for feature_names in combinations(features, order):
        groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
        for row, cats in categorized:
            values = tuple(cats[name] for name in feature_names)
            if "UNKNOWN" in values:
                continue
            groups[values].append(row)
        for values, sample in groups.items():
            if len(sample) < min_observations:
                continue
            output.append(_summarize(sample, baseline, feature_names, values))
    return sorted(output, key=lambda x: (x["lift_1_0r_pp"], x["observations"]), reverse=True)


async def run_edge_interaction_discovery(
    provider,
    symbols: list[str],
    start_date: str,
    end_date: str,
    max_observations: int = 600,
    round_trip_cost_bps: float = 10.0,
    sample_every_bars: int = 3,
    min_interaction_observations: int = 30,
):
    base = await run_edge_discovery(
        provider,
        symbols,
        start_date,
        end_date,
        max_observations=max_observations,
        round_trip_cost_bps=round_trip_cost_bps,
        sample_every_bars=sample_every_bars,
    )
    observations = list(base.get("observations") or [])
    baseline = _baseline(observations)
    minimum = max(20, min(int(min_interaction_observations), 250))
    pairwise = _interaction_rows(observations, baseline, minimum, 2)
    # Three-way interactions are intentionally restricted to combinations containing HIGH ATR,
    # because HIGH ATR is the first replicated feature and this avoids an uncontrolled search explosion.
    triple_all = _interaction_rows(observations, baseline, minimum, 3)
    three_way = [x for x in triple_all if "premium_atr_pct" in x["features"] and x["values"][x["features"].index("premium_atr_pct")] == "HIGH"]
    return {
        "mode": "ALPHAPILOT_EDGE_DISCOVERY_V2_INTERACTIONS",
        "research_only": True,
        "production_rules_changed": False,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": symbols,
        "round_trip_cost_bps": round_trip_cost_bps,
        "sample_every_bars": sample_every_bars,
        "min_interaction_observations": minimum,
        "baseline": baseline,
        "pairwise_interactions": pairwise,
        "high_atr_three_way_interactions": three_way,
        "source_errors": base.get("errors") or [],
        "limitations": [
            "V2 analyzes interactions only after v1 produced a replicated HIGH premium-ATR feature.",
            "Pairwise buckets use fixed v1 thresholds; thresholds are not optimized from this result.",
            "Three-way search is restricted to combinations containing HIGH ATR to limit multiple-testing risk.",
            f"Interactions with fewer than {minimum} observations are excluded.",
            "PROMISING/WATCH/WEAK are research labels only and cannot authorize production trades.",
            "Any candidate interaction must be frozen and retested on untouched symbols and dates.",
        ],
    }
