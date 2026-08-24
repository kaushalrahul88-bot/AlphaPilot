from __future__ import annotations

from datetime import datetime, timezone

from .news import _fetch_google_news

TOPICS = {
    "INDIA_MACRO": 'India RBI SEBI government economy inflation policy markets when:2d',
    "GLOBAL_MACRO": 'Federal Reserve ECB BOJ inflation interest rates bond yields dollar global markets when:2d',
    "GEOPOLITICS": 'geopolitics war sanctions ceasefire tariffs trade dispute Iran Israel Russia Ukraine China Taiwan when:2d',
    "ENERGY": 'crude oil Brent WTI OPEC Hormuz LNG natural gas energy supply disruption when:2d',
    "COMMODITIES": 'gold silver copper commodities prices global markets when:2d',
    "CHINA_ASIA": 'China economy stimulus PMI Japan BOJ Asia markets when:2d',
    "GLOBAL_RISK": 'S&P 500 Nasdaq VIX US stocks global risk markets when:2d',
}

HIGH_IMPACT_TERMS = (
    "war", "attack", "missile", "sanction", "tariff", "emergency", "rate cut", "rate hike",
    "federal reserve", "rbi", "opec", "hormuz", "ceasefire", "default", "crisis", "inflation",
)


def _impact(headline: str) -> str:
    text = headline.lower()
    hits = sum(1 for term in HIGH_IMPACT_TERMS if term in text)
    return "HIGH" if hits >= 2 else "MEDIUM" if hits == 1 else "LOW"


def _risk_state(rows: list[dict]) -> str:
    bull = sum(1 for r in rows if r.get("sentiment") == "BULLISH")
    bear = sum(1 for r in rows if r.get("sentiment") == "BEARISH")
    if bear >= bull + 3:
        return "RISK_OFF"
    if bull >= bear + 3:
        return "RISK_ON"
    return "NEUTRAL"


async def global_intelligence(limit_per_topic: int = 5) -> dict:
    limit = max(2, min(int(limit_per_topic), 8))
    topics: dict[str, list[dict]] = {}
    all_rows: list[dict] = []
    for name, query in TOPICS.items():
        rows = await _fetch_google_news(query, f"global-intelligence:v1:{name}", limit)
        enriched = [{**row, "topic": name, "impact": _impact(str(row.get("headline", "")))} for row in rows]
        topics[name] = enriched
        all_rows.extend(enriched)
    high_impact = [r for r in all_rows if r.get("impact") == "HIGH"]
    return {
        "mode": "ALPHAPILOT_GLOBAL_INTELLIGENCE_V1",
        "research_only": True,
        "production_rules_changed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "risk_state": _risk_state(all_rows),
        "topics": topics,
        "high_impact": high_impact[:12],
        "method_note": "News is Market Brain context only. Sentiment and impact are research annotations and do not authorize or veto production trades.",
    }
