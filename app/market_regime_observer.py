from __future__ import annotations

def observe_regime(features:dict)->dict:
    """Describe the trading environment; this is not a directional forecast."""
    trend=features.get("trend_structure","UNKNOWN")
    vol=features.get("volatility_regime","UNKNOWN")
    location=features.get("location","UNKNOWN")
    participation=features.get("participation","UNKNOWN")
    opening=features.get("opening_behavior","UNKNOWN")
    labels=[]
    if trend in {"UPTREND","DOWNTREND"}:labels.append("TRENDING")
    elif trend=="RANGE":labels.append("RANGING")
    if vol=="HIGH":labels.append("HIGH_VOLATILITY")
    elif vol=="LOW":labels.append("LOW_VOLATILITY")
    if opening in {"BREAKOUT","BREAKDOWN"}:labels.append("OPENING_EXPANSION")
    if location in {"EXTENDED_ABOVE_VALUE","EXTENDED_BELOW_VALUE"}:labels.append("EXTENDED")
    if participation=="WEAKENING":labels.append("PARTICIPATION_FADING")
    return {"mode":"MARKET_REGIME_OBSERVER_V1","regime_labels":labels or ["UNCLASSIFIED"],
      "observations":{"trend_structure":trend,"volatility_regime":vol,"location":location,
                      "participation":participation,"opening_behavior":opening},
      "strategy_implications":strategy_implications(labels),
      "rule":"Regime constrains which trade ideas make sense; it does not predict the next move."}

def strategy_implications(labels:list[str])->list[str]:
    out=[]
    if "TRENDING" in labels:out.append("Prefer continuation/pullback hypotheses over blind mean reversion.")
    if "RANGING" in labels:out.append("Demand edge-location entries; avoid chasing the middle of value.")
    if "HIGH_VOLATILITY" in labels:out.append("Require wider structural invalidation or smaller risk; avoid arbitrary tight stops.")
    if "EXTENDED" in labels:out.append("Avoid chasing; require consolidation, pullback, or fresh confirmation.")
    if "PARTICIPATION_FADING" in labels:out.append("Treat breakout continuation with caution.")
    return out
