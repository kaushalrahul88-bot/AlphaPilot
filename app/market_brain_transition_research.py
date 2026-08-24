from __future__ import annotations

from collections import defaultdict

from .market_brain_context_research import FEATURES


def _transition_label(prev: dict, cur: dict, feature: str) -> str | None:
    a, b = str(prev.get(feature, "UNKNOWN")), str(cur.get(feature, "UNKNOWN"))
    if "UNKNOWN" in {a, b}:
        return None
    return f"{feature}={a}→{b}"


def transition_summaries(observations: list[dict], min_obs: int = 20) -> list[dict]:
    if len(observations) < 2:
        return []
    baseline = sum(float(x.get("fwd60", 0.0)) for x in observations) / len(observations)
    groups = defaultdict(list)
    ordered = sorted(observations, key=lambda x: str(x.get("ts", "")))
    for i in range(1, len(ordered)):
        prev, cur = ordered[i - 1], ordered[i]
        if str(prev.get("ts", ""))[:10] != str(cur.get("ts", ""))[:10]:
            continue
        for feature in FEATURES:
            label = _transition_label(prev, cur, feature)
            if label:
                groups[label].append(cur)

    out = []
    for label, sample in groups.items():
        n = len(sample)
        if n < min_obs:
            continue
        avg = lambda k: sum(float(x.get(k, 0.0)) for x in sample) / n
        avg60 = avg("fwd60")
        pos = sum(float(x.get("fwd60", 0.0)) > .15 for x in sample) / n * 100
        neg = sum(float(x.get("fwd60", 0.0)) < -.15 for x in sample) / n * 100
        state = "LOW_SAMPLE" if n < 30 else "BULLISH_LEAN" if avg60 >= .12 and pos >= 55 else "BEARISH_LEAN" if avg60 <= -.12 and neg >= 55 else "MIXED"
        out.append({
            "label": label,
            "observations": n,
            "avg15": round(avg("fwd15"), 4),
            "avg30": round(avg("fwd30"), 4),
            "avg60": round(avg60, 4),
            "lift60": round(avg60 - baseline, 4),
            "positive60_pct": round(pos, 1),
            "negative60_pct": round(neg, 1),
            "state": state,
        })
    return sorted(out, key=lambda x: (abs(x["avg60"]), x["observations"]), reverse=True)
