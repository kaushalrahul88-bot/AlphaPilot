from __future__ import annotations

from datetime import datetime, timezone

from .news import _fetch_google_news

OFFICIAL_WATCH = {
    "INDIA": {
        "query": '(site:rbi.org.in OR site:sebi.gov.in OR site:pib.gov.in OR site:pmindia.gov.in) (speech OR statement OR press release OR policy OR address) when:3d',
        "institutions": ("rbi", "sebi", "pib", "prime minister", "pm india", "government of india"),
        "affected": ["NIFTY", "BANKNIFTY", "INR", "INDIA_RATES"],
    },
    "UNITED_STATES": {
        "query": '(site:whitehouse.gov OR site:federalreserve.gov OR site:treasury.gov) (speech OR remarks OR statement OR press release OR sanctions OR tariffs OR rates) when:3d',
        "institutions": ("white house", "federal reserve", "treasury", "president", "fed chair"),
        "affected": ["GLOBAL_RISK", "NIFTY", "IT", "USDINR", "BONDS"],
    },
    "EUROPE": {
        "query": '(site:ecb.europa.eu OR site:consilium.europa.eu OR site:ec.europa.eu) (speech OR remarks OR statement OR rates OR sanctions) when:3d',
        "institutions": ("ecb", "european central bank", "european council", "european commission"),
        "affected": ["GLOBAL_RISK", "EURO", "IT", "METALS"],
    },
    "JAPAN_ASIA": {
        "query": '(site:boj.or.jp OR site:gov.cn OR site:mofa.go.jp) (speech OR statement OR policy OR rates OR stimulus) when:3d',
        "institutions": ("bank of japan", "boj", "china", "japan"),
        "affected": ["ASIA_RISK", "METALS", "NIFTY", "JPY"],
    },
    "GEOPOLITICAL_LEADERS": {
        "query": '(president OR prime minister OR foreign ministry OR defence ministry) (Iran OR Israel OR Russia OR Ukraine OR China OR Taiwan) (statement OR speech OR remarks OR post) when:2d',
        "institutions": ("president", "prime minister", "foreign ministry", "defence ministry"),
        "affected": ["NIFTY", "CRUDEOIL", "DEFENCE", "AIRLINES", "METALS"],
    },
    "ENERGY_POLICY": {
        "query": '(site:opec.org OR site:iea.org OR site:eia.gov) (statement OR report OR press release OR production OR supply) when:3d',
        "institutions": ("opec", "iea", "eia"),
        "affected": ["CRUDEOIL", "NATURALGAS", "ONGC", "OIL", "RELIANCE", "AIRLINES"],
    },
}

OFFICIAL_SOURCE_HINTS = (
    "rbi", "sebi", "pib", "pm india", "white house", "federal reserve", "treasury", "ecb",
    "european commission", "bank of japan", "boj", "opec", "iea", "eia", "government", "ministry",
)

SOCIAL_HINTS = (" x.com", " twitter", "truth social", "facebook", "instagram", "telegram", "social media")
SPEECH_HINTS = ("speech", "remarks", "address", "press conference", "statement", "press release", "testimony")


def _source_type(headline: str, source: str) -> str:
    text = f"{headline} {source}".lower()
    if any(h in text for h in SOCIAL_HINTS):
        return "OFFICIAL_SOCIAL_REFERENCE"
    if any(h in text for h in SPEECH_HINTS):
        return "OFFICIAL_SPEECH_OR_STATEMENT"
    return "OFFICIAL_RELEASE_REFERENCE"


def _confidence(source: str, headline: str) -> str:
    text = f"{source} {headline}".lower()
    if any(h in text for h in OFFICIAL_SOURCE_HINTS):
        return "HIGH"
    return "MEDIUM"


def _novelty(headline: str) -> str:
    text = headline.lower()
    if any(x in text for x in ("announces", "new", "unexpected", "emergency", "immediate", "first time", "surprise")):
        return "NEW_INFORMATION"
    if any(x in text for x in ("reiterates", "repeats", "reaffirms", "again", "maintains")):
        return "REITERATION"
    return "UNASSESSED"


async def official_signals(limit_per_group: int = 4) -> dict:
    limit = max(2, min(int(limit_per_group), 6))
    groups: dict[str, list[dict]] = {}
    all_rows: list[dict] = []
    for group, cfg in OFFICIAL_WATCH.items():
        rows = await _fetch_google_news(str(cfg["query"]), f"official-signals:v1:{group}", min(10, limit * 2))
        enriched: list[dict] = []
        for row in rows:
            headline = str(row.get("headline", ""))
            source = str(row.get("source", ""))
            text = f"{headline} {source}".lower()
            if not any(token in text for token in cfg["institutions"]):
                continue
            enriched.append({
                **row,
                "group": group,
                "source_type": _source_type(headline, source),
                "confidence": _confidence(source, headline),
                "novelty": _novelty(headline),
                "affected": cfg["affected"],
                "primary_source_preferred": True,
            })
        groups[group] = enriched[:limit]
        all_rows.extend(groups[group])
    return {
        "mode": "ALPHAPILOT_OFFICIAL_SIGNALS_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "production_rules_changed": False,
        "groups": groups,
        "items": all_rows,
        "method_note": "Official Signals prioritizes speeches, statements, official releases and references to verified official social posts. Direct social-platform ingestion is used only when a supported authenticated API/feed is available; otherwise AlphaPilot records corroborated references rather than pretending it read a private or inaccessible post directly.",
    }
