from __future__ import annotations

from datetime import datetime, timezone

from .news import _fetch_google_news

OFFICIAL_WATCH = {
    "INDIA": {
        "query": '(site:rbi.org.in OR site:sebi.gov.in OR site:pib.gov.in OR site:pmindia.gov.in) (RBI OR SEBI OR economy OR inflation OR rates OR liquidity OR rupee OR budget OR tax OR banking OR markets OR trade OR energy OR infrastructure) when:3d',
        "institutions": ("rbi", "sebi", "pib", "prime minister", "pm india", "government of india"),
        "primary_sources": ("rbi", "sebi", "pib", "prime minister's office", "pm india", "government of india"),
    },
    "UNITED_STATES": {
        "query": '(site:whitehouse.gov OR site:federalreserve.gov OR site:treasury.gov) (Federal Reserve OR rates OR inflation OR tariffs OR sanctions OR trade OR treasury OR economy OR dollar OR energy) when:3d',
        "institutions": ("white house", "federal reserve", "treasury", "president", "fed chair"),
        "primary_sources": ("white house", "federal reserve", "u.s. department of the treasury", "us department of the treasury", "treasury (.gov)", "federal reserve (.gov)"),
    },
    "EUROPE": {
        "query": '(site:ecb.europa.eu OR site:consilium.europa.eu OR site:ec.europa.eu) (ECB OR rates OR inflation OR sanctions OR tariffs OR trade OR economy OR energy) when:3d',
        "institutions": ("ecb", "european central bank", "european council", "european commission"),
        "primary_sources": ("european central bank", "european commission", "european council"),
    },
    "JAPAN_ASIA": {
        "query": '(site:boj.or.jp OR site:gov.cn OR site:mofa.go.jp) (rates OR stimulus OR inflation OR currency OR tariffs OR trade OR economy OR sanctions) when:3d',
        "institutions": ("bank of japan", "boj", "china", "japan"),
        "primary_sources": ("bank of japan", "boj", "government of japan", "ministry of foreign affairs of japan", "state council", "gov.cn"),
    },
    "GEOPOLITICAL_LEADERS": {
        "query": '(site:gov OR site:gov.uk OR site:gov.il OR site:president.gov.ua OR site:kremlin.ru OR site:gov.cn) (president OR prime minister OR foreign ministry OR defence ministry) (Iran OR Israel OR Russia OR Ukraine OR China OR Taiwan) (sanctions OR ceasefire OR attack OR war OR tariffs OR trade OR oil OR shipping OR Hormuz) when:2d',
        "institutions": ("president", "prime minister", "foreign ministry", "defence ministry", "ministry of foreign affairs"),
        "primary_sources": ("government", "president", "prime minister", "foreign ministry", "ministry of foreign affairs", "defence ministry", "kremlin"),
    },
    "ENERGY_POLICY": {
        "query": '(site:opec.org OR site:iea.org OR site:eia.gov) (oil OR crude OR natural gas OR LNG OR production OR supply OR inventory OR demand OR OPEC) when:3d',
        "institutions": ("opec", "iea", "eia"),
        "primary_sources": ("opec", "international energy agency", "iea", "u.s. energy information administration", "eia"),
    },
}

SOCIAL_HINTS = (" x.com", " twitter", "truth social", "facebook", "instagram", "telegram", "social media")
SPEECH_HINTS = ("speech", "remarks", "address", "press conference", "statement", "press release", "testimony", "interview")

MARKET_TERMS = {
    "rates": ("rate", "interest", "yield", "bond", "treasury", "liquidity"),
    "inflation": ("inflation", "cpi", "pce", "prices"),
    "fx": ("rupee", "dollar", "currency", "forex", "yen", "euro"),
    "trade": ("tariff", "trade", "export", "import", "sanction"),
    "energy": ("oil", "crude", "opec", "hormuz", "lng", "natural gas", "petroleum", "energy"),
    "growth": ("gdp", "growth", "economy", "recession", "employment", "jobs", "stimulus", "industrial production"),
    "markets": ("market", "banking", "sebi", "rbi", "federal reserve", "ecb", "boj"),
    "geopolitics": ("war", "attack", "ceasefire", "missile", "shipping", "strait", "defence"),
}

NOISE_TERMS = (
    "award", "teachers day", "pensioners", "sanitation entrepreneur", "education freedom", "grand prix",
    "videos – page", "videos - page", "consultation", "clean cooking", "energy efficiency for business",
    "sports", "festival", "felicitated", "commemoration", "anniversary", "tourism", "scholarship",
    "call for papers", "job search", "career", "vacancy", "emergency rental assistance",
    "data download program", "database|", "database |", "notifications|", "notifications |",
    "draft directions", "speeches & media interactions", "upcoming auctions data", "daily treasury rates",
    "international -", "emissions factors", "page 1 of", "page 2 of", "calendar:", "calendar ",
    "gift contributions", "trust fund", "portal", "hydrogen review", "optimal monetary and fiscal policy",
    "video message", "conference", "paper", "research paper", "working paper",
)

GENERIC_TITLES = {
    "international", "database", "notifications", "speeches & media interactions", "draft directions",
    "daily treasury rates", "treasury securities upcoming auctions data", "calendar", "eu f&t portal",
}


def _source_type(headline: str, source: str) -> str:
    text = f"{headline} {source}".lower()
    if any(h in text for h in SOCIAL_HINTS): return "OFFICIAL_SOCIAL_REFERENCE"
    if any(h in text for h in SPEECH_HINTS): return "OFFICIAL_SPEECH_OR_STATEMENT"
    return "OFFICIAL_RELEASE_REFERENCE"


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
    text = " ".join(headline.lower().split()).strip(" -|:")
    if not text or len(text) < 18: return False
    if text in GENERIC_TITLES: return False
    if any(term in text for term in NOISE_TERMS): return False
    return bool(_market_topics(headline))


def _is_primary_source(source: str, cfg: dict) -> bool:
    text = source.lower()
    return any(token in text for token in cfg.get("primary_sources", ()))


def _impact(headline: str) -> str:
    text = headline.lower()
    high = ("rate hike", "rate cut", "sanction", "tariff", "war", "attack", "ceasefire", "hormuz", "emergency", "surprise")
    medium = ("inflation", "liquidity", "oil", "crude", "treasury", "stimulus", "trade", "gdp", "currency", "production", "inventory")
    if any(x in text for x in high): return "HIGH"
    if any(x in text for x in medium): return "MEDIUM"
    return "LOW"


def _affected(group: str, headline: str) -> list[str]:
    topics = set(_market_topics(headline)); out: list[str] = []
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
    limit = max(2, min(int(limit_per_group), 6)); groups: dict[str, list[dict]] = {}; all_rows: list[dict] = []
    dropped_irrelevant = 0; dropped_non_primary = 0
    for group, cfg in OFFICIAL_WATCH.items():
        rows = await _fetch_google_news(str(cfg["query"]), f"official-signals:v1.5:{group}", min(16, limit * 5))
        enriched: list[dict] = []; seen: set[str] = set()
        for row in rows:
            headline = str(row.get("headline", "")); source = str(row.get("source", "")); text = f"{headline} {source}".lower()
            if not any(token in text for token in cfg["institutions"]): continue
            if not _is_primary_source(source, cfg): dropped_non_primary += 1; continue
            if not _market_relevant(headline): dropped_irrelevant += 1; continue
            key = " ".join(headline.lower().split())
            if key in seen: continue
            seen.add(key)
            enriched.append({**row, "group": group, "source_type": _source_type(headline, source), "confidence": "HIGH", "novelty": _novelty(headline), "market_relevance": "MARKET_RELEVANT", "market_topics": _market_topics(headline), "impact": _impact(headline), "affected": _affected(group, headline), "primary_source": True})
        enriched.sort(key=lambda r: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(str(r.get("impact")), 3), 0 if r.get("novelty") == "NEW_INFORMATION" else 1))
        groups[group] = enriched[:limit]; all_rows.extend(groups[group])
    return {"mode": "ALPHAPILOT_OFFICIAL_SIGNALS_V1_5", "generated_at": datetime.now(timezone.utc).isoformat(), "research_only": True, "production_rules_changed": False, "groups": groups, "items": all_rows, "quality": {"accepted_items": len(all_rows), "dropped_irrelevant": dropped_irrelevant, "dropped_non_primary": dropped_non_primary}, "method_note": "Official Signals v1.5 is primary-source only and excludes generic data, archive, calendar, research-paper and portal pages. Corroborated media reports stay in Global News rather than masquerading as official signals. Direct social-platform ingestion is only used when a supported authenticated API/feed exists."}
