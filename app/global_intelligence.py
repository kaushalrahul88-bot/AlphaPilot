from __future__ import annotations

from datetime import datetime, timezone

from .cross_assets import cross_asset_snapshot
from .news import _fetch_google_news
from .official_signals import official_signals

TOPICS = {
    "INDIA_MACRO": 'India (RBI OR SEBI OR inflation OR GDP OR rupee OR budget OR government policy) markets when:2d',
    "GLOBAL_MACRO": '(Federal Reserve OR Fed OR ECB OR BOJ OR inflation OR interest rates OR bond yields OR dollar) markets when:2d',
    "GEOPOLITICS": '(Iran OR Israel OR Russia OR Ukraine OR China OR Taiwan OR sanctions OR ceasefire OR tariffs OR war) markets when:2d',
    "ENERGY": '(crude oil OR Brent OR WTI OR OPEC OR Hormuz OR LNG OR natural gas) when:2d',
    "COMMODITIES": '(gold OR silver OR copper OR commodities) prices markets when:2d',
    "CHINA_ASIA": '(China economy OR China stimulus OR China PMI OR Japan BOJ OR Asia markets) when:2d',
    "GLOBAL_RISK": '(S&P 500 OR Nasdaq OR VIX OR Wall Street OR US stocks) when:2d',
}

TOPIC_ANCHORS = {
    "INDIA_MACRO": ("rbi", "sebi", "india", "indian", "rupee", "sbi", "government of india", "g-sec"),
    "GLOBAL_MACRO": ("federal reserve", "fed ", "ecb", "boj", "treasury", "dollar index", "bond yield", "pce", "us inflation"),
    "GEOPOLITICS": ("iran", "israel", "russia", "ukraine", "taiwan", "sanction", "ceasefire", "tariff", "trade war", "attack", "war"),
    "ENERGY": ("oil", "brent", "wti", "opec", "hormuz", "lng", "natural gas", "petroleum"),
    "COMMODITIES": ("gold", "silver", "copper", "commodity", "commodities", "metal"),
    "CHINA_ASIA": ("china", "chinese", "japan", "boj", "nikkei", "hang seng", "asia markets", "asian markets", "china pmi", "china stimulus"),
    "GLOBAL_RISK": ("s&p 500", "nasdaq", "dow", "wall street", "vix", "us stock", "us market", "us futures"),
}

TOPIC_NOISE = {
    "INDIA_MACRO": ("gold rally", "gold prices", "silver", "jackson hole"),
    "CHINA_ASIA": ("expo", "lululemon", "sporting goods", "investment in india"),
}

TIER_1_SOURCES = (
    "reuters", "associated press", "ap news", "bloomberg", "financial times", "wall street journal",
    "new york times", "cnbc", "bbc", "the economic times", "business standard", "mint", "livemint",
    "moneycontrol", "pib",
)
TIER_2_SOURCES = (
    "fxstreet", "investing.com", "marketwatch", "barron's", "fortune", "forbes", "business insider",
    "the hindu", "indian express", "hindustan times", "energy now", "energynews",
)

HIGH_IMPACT_TERMS = (
    "war", "attack", "missile", "sanction", "tariff", "emergency", "rate cut", "rate hike",
    "federal reserve", "rbi", "opec", "hormuz", "ceasefire", "default", "crisis", "inflation",
)

AFFECTED = {
    "INDIA_MACRO": ["NIFTY", "BANKNIFTY", "INR", "INDIA_RATES"],
    "GLOBAL_MACRO": ["NIFTY", "IT", "BANKS", "USDINR", "GLOBAL_RISK"],
    "GEOPOLITICS": ["NIFTY", "CRUDEOIL", "DEFENCE", "AIRLINES", "METALS"],
    "ENERGY": ["CRUDEOIL", "ONGC", "OIL", "RELIANCE", "AIRLINES", "PAINTS"],
    "COMMODITIES": ["METALS", "HINDALCO", "TATASTEEL", "HINDZINC"],
    "CHINA_ASIA": ["METALS", "IT", "NIFTY", "ASIA_RISK"],
    "GLOBAL_RISK": ["NIFTY", "BANKNIFTY", "IT", "GLOBAL_RISK"],
}


def _source_tier(source: str) -> str:
    text = source.lower()
    if any(name in text for name in TIER_1_SOURCES): return "TIER_1"
    if any(name in text for name in TIER_2_SOURCES): return "TIER_2"
    return "TIER_3"


def _relevant(topic: str, headline: str) -> bool:
    text = headline.lower()
    if any(term in text for term in TOPIC_NOISE.get(topic, ())): return False
    return any(term in text for term in TOPIC_ANCHORS.get(topic, ()))


def _impact(headline: str, source_tier: str) -> str:
    text = headline.lower(); hits = sum(1 for term in HIGH_IMPACT_TERMS if term in text)
    if hits >= 2 and source_tier != "TIER_3": return "HIGH"
    if hits >= 1: return "MEDIUM"
    return "LOW"


def _risk_score(row: dict) -> float:
    sentiment = row.get("sentiment")
    if sentiment not in {"BULLISH", "BEARISH"}: return 0.0
    direction = 1.0 if sentiment == "BULLISH" else -1.0
    impact_weight = {"HIGH": 3.0, "MEDIUM": 1.5, "LOW": 0.5}.get(str(row.get("impact")), 0.5)
    source_weight = {"TIER_1": 1.0, "TIER_2": 0.65, "TIER_3": 0.2}.get(str(row.get("source_tier")), 0.2)
    return direction * impact_weight * source_weight


def _risk_state(rows: list[dict]) -> tuple[str, float]:
    score = round(sum(_risk_score(r) for r in rows), 2)
    if score <= -3.0: return "RISK_OFF", score
    if score >= 3.0: return "RISK_ON", score
    return "NEUTRAL", score


def _dedupe(rows: list[dict]) -> list[dict]:
    out: list[dict] = []; seen: set[str] = set()
    for row in rows:
        key = " ".join(str(row.get("headline", "")).lower().split())
        if not key or key in seen: continue
        seen.add(key); out.append(row)
    return out


async def global_intelligence(limit_per_topic: int = 5) -> dict:
    limit = max(2, min(int(limit_per_topic), 8)); topics: dict[str, list[dict]] = {}; all_rows: list[dict] = []; dropped_irrelevant = 0; global_seen: set[str] = set()
    for name, query in TOPICS.items():
        raw = await _fetch_google_news(query, f"global-intelligence:v1.6:{name}", min(14, limit * 3)); enriched: list[dict] = []
        for row in raw:
            headline = str(row.get("headline", "")); key = " ".join(headline.lower().split())
            if not _relevant(name, headline): dropped_irrelevant += 1; continue
            if key in global_seen: continue
            tier = _source_tier(str(row.get("source", "")))
            enriched.append({**row, "topic": name, "source_tier": tier, "impact": _impact(headline, tier), "affected": AFFECTED.get(name, [])})
            global_seen.add(key)
        enriched = _dedupe(enriched)
        enriched.sort(key=lambda r: ({"TIER_1": 0, "TIER_2": 1, "TIER_3": 2}.get(str(r.get("source_tier")), 3), {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(str(r.get("impact")), 3)))
        topics[name] = enriched[:limit]; all_rows.extend(topics[name])

    official = await official_signals(min(limit, 4)); cross_assets = await cross_asset_snapshot(); risk_state, risk_score = _risk_state(all_rows)
    high_impact = [r for r in all_rows if r.get("impact") == "HIGH" and r.get("source_tier") != "TIER_3"]
    oq = official.get("quality", {})
    return {
        "mode": "ALPHAPILOT_GLOBAL_INTELLIGENCE_V1_6",
        "research_only": True,
        "production_rules_changed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "risk_state": risk_state,
        "risk_score": risk_score,
        "topics": topics,
        "high_impact": high_impact[:12],
        "official_signals": official,
        "cross_assets": cross_assets,
        "quality": {
            "accepted_items": len(all_rows), "dropped_irrelevant": dropped_irrelevant,
            "tier_1_items": sum(1 for r in all_rows if r.get("source_tier") == "TIER_1"),
            "tier_2_items": sum(1 for r in all_rows if r.get("source_tier") == "TIER_2"),
            "tier_3_items": sum(1 for r in all_rows if r.get("source_tier") == "TIER_3"),
            "official_signal_items": len(official.get("items", [])),
            "official_dropped_irrelevant": int(oq.get("dropped_irrelevant", 0)),
            "official_dropped_non_primary": int(oq.get("dropped_non_primary", 0)),
        },
        "method_note": "Global Intelligence v1.6 adds research-only cross-asset snapshots for India VIX, USDINR, DXY, US 10Y, Brent and Nasdaq futures. Public cross-asset values are contextual only and cannot authorize or veto production trades.",
    }
