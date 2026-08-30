from __future__ import annotations
import hashlib, json, re
from collections import Counter, defaultdict
from .copper_historical_news import fetch_copper_historical_news
from .copper_market_brain_direction_audit import PRIMARY_START, PRIMARY_END

STRONG_MARKET_TERMS=(
 "copper price","copper prices","lme copper","comex copper","mcx copper",
 "inventory","inventories","warehouse stocks","treatment charge","treatment charges",
 "smelter","smelting","refinery","refining","concentrate exports","concentrate export",
 "copper supply","copper demand","copper production","copper output","copper imports","copper exports",
 "china copper","chinese copper","copper tariff","copper tariffs","copper shortage",
)
GLOBAL_MACRO_TERMS=("china","chinese","pmi","manufacturing","stimulus","tariff","tariffs","trade war","dollar","fed","federal reserve","rates")
SUPPLY_EVENT_TERMS=("strike","disruption","shutdown","closure","ban","bans","export ban","accident","flood","earthquake","fire")
COPPER_ASSET_TERMS=("codelco","escondida","grasberg","collahuasi","las bambas","kamoa","tenke","chuquicamata")
MAJOR_SUPPLY_TERMS=COPPER_ASSET_TERMS+("freeport","antofagasta","glencore","bhp","rio tinto","southern copper")
EQUITY_OR_PROJECT_TERMS=("shares","share price","stock","stocks","ofs","ipo","private placement","drilling","drill","exploration","project","earnings","profit","profits","valuation","returns")
PRICE_RECAP_TERMS=("price surge","prices surge","price rises","prices rise","price rally","prices rally","record-high copper prices","record high copper prices","copper gains","copper jumps","copper climbs","copper falls","copper drops","copper slides")
PROMOTIONAL_DEMAND_TERMS=("targets rising","targeting rising","sees rising","expects rising","bets on rising","aims to capitalize","positions for rising")
NON_PRICE_COPPER_TERMS=("wire","cable","antenna","coil","manure","contamination","pig","sports","basketball","mercury beat","school burglary","thief","thieves","stealing","stolen","swan boats")

HARD_IRRELEVANT=(
 "copper theft","stolen copper","copper thief","copper thieves","copper cookware","copper pan",
 "copper pot","copper mug","copper bottle","copper pipe repair","plumbing","copper plumbing",
 "copper iud","copper coil contrace","copper bracelet","copper jewelry","copper jewellery",
 "copper hair","copper color","copper colour","copper dress","copper kitchen","copper sink",
 "copperhead snake","copperhead snakes","copper mountain ski","copper mountain resort",
)
RETROSPECTIVE=("last week","last month","earlier this month","earlier this year","yesterday","recap","weekly review","week in review")
DIRECTIONAL_TERMS=("rise","rises","rally","rallies","jump","jumps","surge","surges","fall","falls","drop","drops","slump","slumps","plunge","plunges","gain","gains")

def _norm_title(title):
    text=re.sub(r"\s+[-–—|:]\s+[^-–—|:]{2,80}$","",str(title or "").lower())
    return re.sub(r"[^a-z0-9]+"," ",text).strip()

def _audit_one(record):
    value=record.get("value") or {}; title=str(value.get("headline") or "").strip(); low=title.lower()
    language=str(value.get("language") or "").lower()
    reasons=[]
    if not title:return "REJECT",["MISSING_HEADLINE"]
    if language and language not in {"english","en"}:return "UNCERTAIN",["NON_ENGLISH_HEADLINE"]
    if any(x in low for x in HARD_IRRELEVANT) or any(x in low for x in NON_PRICE_COPPER_TERMS):
        return "REJECT",["NON_MARKET_COPPER_MEANING"]
    explicit="copper" in low
    named_copper_asset=any(x in low for x in COPPER_ASSET_TERMS)
    if not explicit and not named_copper_asset and not any(x in low for x in ("lme copper","comex copper","mcx copper")):
        return "REJECT",["NO_EXPLICIT_COPPER_OR_NAMED_COPPER_ASSET_REFERENCE"]
    strong=[x for x in STRONG_MARKET_TERMS if x in low]
    macro=[x for x in GLOBAL_MACRO_TERMS if x in low]
    supply_event=[x for x in SUPPLY_EVENT_TERMS if x in low]
    major_supply=[x for x in MAJOR_SUPPLY_TERMS if x in low]
    equity_project=[x for x in EQUITY_OR_PROJECT_TERMS if x in low]
    price_recap=[x for x in PRICE_RECAP_TERMS if x in low]
    promotional=[x for x in PROMOTIONAL_DEMAND_TERMS if x in low]
    # Price-following stories are consequences/recaps, not independent causal news.
    # Keep them visible for audit/context, but never let them vote directionally.
    if price_recap and not (supply_event and major_supply):
        return "UNCERTAIN",["PRICE_RECAP_OR_MARKET_CONSEQUENCE_NOT_INDEPENDENT_NEWS"]
    # Company/project assertions about 'rising demand' are promotional claims rather
    # than independent demand evidence.
    if promotional:
        return "REJECT",["PROMOTIONAL_OR_PROJECT_DEMAND_ASSERTION"]
    # Equity/company/project headlines are not commodity-market evidence merely because
    # the company/project name contains Copper. Keep only when a strong commodity
    # transmission channel is explicit in the same headline.
    if equity_project and not strong and not (supply_event and major_supply):
        return "REJECT",["EQUITY_OR_PROJECT_SPECIFIC_NO_COMMODITY_TRANSMISSION"]
    channels=strong[:]
    if macro and explicit and any(x in low for x in ("demand","imports","manufacturing","pmi","stimulus","tariff","tariffs")):
        channels.extend(macro)
    if supply_event and major_supply:
        channels.extend(supply_event);channels.extend(major_supply)
    if named_copper_asset and any(x in low for x in ("production","output","mine","concentrate","shutdown","closure","strike","disruption")):
        channels.append("NAMED_COPPER_ASSET_SUPPLY")
    if not channels:
        reasons.append("NO_CLEAR_MARKET_TRANSMISSION_CHANNEL")
    if any(x in low for x in RETROSPECTIVE):
        reasons.append("POSSIBLE_RETROSPECTIVE_OR_REPUBLISHED_CONTEXT")
    classification="KEEP" if channels and "POSSIBLE_RETROSPECTIVE_OR_REPUBLISHED_CONTEXT" not in reasons else "UNCERTAIN"
    return classification,reasons or ["CLEAR_COPPER_MARKET_CHANNEL"]

def audit_historical_news_records(records):
    ordered=sorted(records or [],key=lambda x:str(x.get("available_at") or ""))
    rows=[];clusters=defaultdict(list)
    for i,r in enumerate(ordered):
        value=r.get("value") or {}; title=str(value.get("headline") or "")
        cls,reasons=_audit_one(r);key=_norm_title(title)
        row={"index":i,"available_at":r.get("available_at"),"source":r.get("source"),"headline":title,
             "url":value.get("url"),"language":value.get("language"),"sourcecountry":value.get("sourcecountry"),
             "raw_sentiment":value.get("sentiment"),"event_tags":value.get("event_tags") or [],
             "classification":cls,"reasons":reasons,"duplicate_cluster":key}
        rows.append(row);clusters[key].append(row)
    for key,group in clusters.items():
        if not key or len(group)<2:continue
        # Earliest observed copy is retained; later syndicated/republished copies cannot add votes.
        first=group[0]
        for row in group[1:]:
            row["classification"]="DUPLICATE";row["reasons"]=["DUPLICATE_OR_SYNDICATED_HEADLINE",f"EARLIEST={first['available_at']}"]
    counts=Counter(x["classification"] for x in rows)
    accepted=[x for x in rows if x["classification"]=="KEEP"]
    rejected=[x for x in rows if x["classification"]=="REJECT"]
    uncertain=[x for x in rows if x["classification"]=="UNCERTAIN"]
    duplicates=[x for x in rows if x["classification"]=="DUPLICATE"]
    # Only KEEP records may vote directionally. UNCERTAIN stays visible as context with UNKNOWN stance.
    accepted_keys={(x["available_at"],x["headline"]) for x in accepted}
    accepted_records=[]
    for r in ordered:
        v=r.get("value") or {};key=(r.get("available_at"),str(v.get("headline") or ""))
        if key in accepted_keys:accepted_records.append(r)
    digest=hashlib.sha256(json.dumps(accepted_records,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return {"mode":"COPPER_HISTORICAL_NEWS_INTEGRITY_AUDIT_V1","raw_record_count":len(rows),
      "classification_counts":dict(counts),"accepted_record_count":len(accepted_records),
      "accepted_dataset_sha256":digest,"directional_vote_policy":"KEEP_ONLY",
      "uncertain_vote_policy":"VISIBLE_CONTEXT_ONLY_UNKNOWN_STANCE",
      "checks":{"no_rejected_in_accepted":all(x["classification"]=="KEEP" for x in accepted),
                "duplicates_do_not_vote":all(x["classification"]!="KEEP" for x in duplicates),
                "uncertain_do_not_vote":all(x["classification"]!="KEEP" for x in uncertain)},
      "accepted":accepted,"uncertain":uncertain,"rejected":rejected,"duplicates":duplicates,
      "records":rows,"accepted_records":accepted_records,
      "limitations":["Headline-only relevance audit cannot prove the full article's causal interpretation.",
                     "GDELT seendate proves observation by GDELT, not the underlying event occurrence time.",
                     "UNCERTAIN records are retained for manual review but receive zero directional voting power."]}

async def run_historical_news_integrity_audit():
    fetched=await fetch_copper_historical_news(PRIMARY_START,PRIMARY_END)
    audit=audit_historical_news_records(fetched.get("records") or [])
    audit["source_metadata"]={k:v for k,v in fetched.items() if k!="records"}
    return audit
