from __future__ import annotations

from datetime import datetime, timezone

from .news import _fetch_google_news

OFFICIAL_WATCH = {
    "INDIA": {
        "query": '(site:rbi.org.in OR site:sebi.gov.in OR site:pib.gov.in OR site:pmindia.gov.in) (RBI OR SEBI OR economy OR inflation OR rates OR liquidity OR rupee OR budget OR tax OR banking OR markets OR trade OR energy OR infrastructure) when:3d',
        "institutions": ("rbi", "sebi", "pib", "prime minister", "pm india", "government of india"),
    },
    "UNITED_STATES": {
        "query": '(site:whitehouse.gov OR site:federalreserve.gov OR site:treasury.gov) (Federal Reserve OR rates OR inflation OR tariffs OR sanctions OR trade OR treasury OR economy OR dollar OR energy) when:3d',
        "institutions": ("white house", "federal reserve", "treasury", "president", "fed chair"),
    },
    "EUROPE": {
        "query": '(site:ecb.europa.eu OR site:consilium.europa.eu OR site:ec.europa.eu) (ECB OR rates OR inflation OR sanctions OR tariffs OR trade OR economy OR energy) when:3d',
        "institutions": ("ecb", "european central bank", "european council", "european commission"),
    },
    "JAPAN_ASIA": {
        "query": '(site:boj.or.jp OR site:gov.cn OR site:mofa.go.jp) (rates OR stimulus OR inflation OR currency OR tariffs OR trade OR economy OR sanctions) when:3d',
        "institutions": ("bank of japan", "boj", "china", "japan"),
    },
    "GEOPOLITICAL_LEADERS": {
        "query": '(president OR prime minister OR foreign ministry OR defence ministry) (Iran OR Israel OR Russia OR Ukraine OR China OR Taiwan) (sanctions OR ceasefire OR attack OR war OR tariffs OR trade OR oil OR shipping OR Hormuz) when:2d',
        "institutions": ("president", "prime minister", "foreign ministry", "defence ministry"),
    },
    "ENERGY_POLICY": {
        "query": '(site:opec.org OR site:iea.org OR site:eia.gov) (oil OR crude OR natural gas OR LNG OR production OR supply OR inventory OR demand OR OPEC) when:3d',
        "institutions": ("opec", "iea", "eia"),
    },
}

OFFICIAL_SOURCE_HINTS = (
    "rbi", "sebi", "pib", "pm india", "white house", "federal reserve", "treasury", "ecb",
    "european commission", "bank of japan", "boj", "opec", "iea", "eia", "government", "ministry",
)
SOCIAL_HINTS = (" x.com", " twitter", "truth social", "facebook", "instagram", "telegram", "social media")
SPEECH_HINTS = ("speech", "remarks", "address", "press conference", "statement", "press release", "testimony")

MARKET_TERMS = {
    "rates": ("rate", "interest", "yield", "bond", "treasury", "liquidity"),
    "inflation": ("inflation", "cpi", "pce", "prices"),
    "fx": ("rupee", "dollar", "currency", "forex", "yen", "euro"),
    "trade": ("tariff", "trade", "export", "import", "sanction"),
    "energy": ("oil", "crude", "opec", "hormuz", "lng", "natural gas", "petroleum", "energy"),
    "growth": ("gdp", "growth", "economy", "recession", "employment", "jobs", "stimulus"),
    "markets": ("market", "banking", "sebi", "rbi", "federal reserve", "ecb", "boj"),
    "geopolitics": ("war", "attack", "ceasefire", "missile", "shipping", "strait", "defence"),
}

NOISE_TERMS = (
    "award", "teachers day", "pensioners", "sanitation entrepreneur", "education freedom", "grand prix",
    "videos – page", "videos - page", "consultation", "clean cooking", "energy efficiency for business",
    "sports", "festival", "felicitated", "commemoration", "anniversary", "tourism", "scholarship",
)


def _source_type(headline: str, source: str) -> str:
    text = f"{headline} {source}".lower()
    if any(h in text for h in SOCIAL_HINTS):
        return "OFFICIAL_SOCIAL_REFERENCE"
    if any(h in text for h in SPEECH_HINTS):
        return "OFFICIAL_SPEECH_OR_STATEMENT"
    return "OFFICIAL_RELEASE_REFERENCE"


def _confidence(source: str, headline: str) -> str:
    text = f"{source} {headline}".lower()
    return "HIGH" if any(h in text for h in OFFICIAL_SOURCE_HINTS) else "MEDIUM"


def _novelty(headline: str) -> str:
    text = headline.lower()
    if any(x in text for x in ("announces", "new", "unexpected", "emergency", "immediate", "first time", "surprise", "raises", "cuts", "imposes")):
        return "NEW_INFORMATION"
    if any(x in text for x in ("reiterates", "repeats", "reaffirms", "again", "maintains")):
        return "REITERATION"
    return "UNASSESSED"


def _market_topics(headline: str) -> list[str]:
    text = headline.lower()
    return [topic.upper() for topic, terms in MARKET_TERMS.items() if any(term in text for term in terms)]


def _market_relevant(headline: str) -> bool:
    text = headline.lower()
    if any(term in text for term in NOISE_TERMS):
        return False
    return bool(_market_topics(headline))


def _impact(headline: str) -> str:
    text = headline.lower()
    high = ("rate hike", "rate cut", "sanction", "tariff", "war", "attack", "ceasefire", "hormuz", "emergency", "surprise")
    medium = ("inflation", "liquidity", "oil", "crude", "treasury", "stimulus", "trade", "gdp", "currency", "production", "inventory")
    if any(x in text for x in high):
        return "HIGH"
    if any(x in text for x in medium):
        return "MEDIUM"
    return "LOW"


def _affected(group: str, headline: str) -> list[str]:
    topics = set(_market_topics(headline))
    out: list[str] = []
    if group == "INDIA": out += ["NIFTY"]
    if group == "UNITED_STATES": out += ["GLOBAL_RISK", "NIFTY"]
    if group == "EUROPE": out += ["GLOBAL_RISK"]
    if group == "JAPAN_ASIA": out += ["ASIA_RISK", "NIFTY"]
    if group == "GEOPOLITICAL_LEADERS": out += ["NIFTY", "GLOBAL_RISK"]
    if group == "ENERGY_POLICY": out += ["CRUDEOIL", "NATURALGAS"]
    if "RATES" in topics: out += ["BANKNIFTY", "BONDS", "INDIA_RATES"]
    if "FX" in topics: out += ["USDINR", "INR"]
    if "ENERGY" in topics: out += ["CRUDEOIL", "ONGC", "OIL", "RELIANCE", "AIRLINES", "PAINTS"]
    if "TRADE" in topics: out += ["IT", "METALS", "EXPORTERS"]
    if "GEOPOLITICS" in topics: out += ["DEFENCE", "AIRLINES", "CRUDEOIL"]
    if "GROWTH" in topics: out += ["BANKNIFTY", "METALS", "AUTO"]
    return list(dict.fromkeys(out))


async def official_signals(limit_per_group: int = 4) -> dict:
    limit = max(2, min(int(limit_per_group), 6))
    groups: dict[str, list[dict]] = {}
    all_rows: list[dict] = []
    dropped_irrelevant = 0
    for group, cfg in OFFICIAL_WATCH.items():
        rows = await _fetch_google_news(str(cfg["query"]), f"official-signals:v1.3:{group}", min(12, limit * 3))
        enriched: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            headline = str(row.get("headline", ""))
            source = str(row.get("source", ""))
            text = f"{headline} {source}".lower()
            if not any(token in text for token in cfg["institutions"]):
                continue
            if not _market_relevant(headline):
                dropped_irrelevant += 1
                continue
            key = " ".join(headline.lower().split())
            if key in seen:
                continue
            seen.add(key)
            enriched.append({
                **row,
                "group": group,
                "source_type": _source_type(headline, source),
                "confidence": _confidence(source, headline),
                "novelty": _novelty(headline),
                "market_relevance": "MARKET_RELEVANT",
                "market_topics": _market_topics(headline),
                "impact": _impact(headline),
                "affected": _affected(group, headline),
                "primary_source_preferred": True,
            })
        enriched.sort(key=lambda r: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(str(r.get("impact")), 3), 0 if r.get("novelty") == "NEW_INFORMATION" else 1))
        groups[group] = enriched[:limit]
        all_rows.extend(groups[group])
    return {
        "mode": "ALPHAPILOT_OFFICIAL_SIGNALS_V1_3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "production_rules_changed": False,
        "groups": groups,
        "items": all_rows,
        "quality": {"accepted_items": len(all_rows), "dropped_irrelevant": dropped_irrelevant},
        "method_note": "Official Signals v1.3 only surfaces market-relevant speeches/releases, ranks direct impact, and maps affected assets from the event topic rather than assigning every official release to the whole market. Direct social-platform ingestion is only used when a supported authenticated API/feed exists.",
    }
