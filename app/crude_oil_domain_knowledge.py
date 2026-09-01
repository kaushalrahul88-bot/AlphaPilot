"""Research-only Crude Oil domain knowledge for AlphaPilot.

This module mirrors the governance of the Copper domain-knowledge layer while
remaining commodity-specific.  Knowledge supplies mechanisms, conditional
priors, exceptions and research hooks; it never creates BUY_CE/BUY_PE orders.
Historical event/news records are separate and must still satisfy point-in-time
availability before they can enter a replay.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

SourceTier = Literal["A_PRIMARY", "B_RESEARCH", "C_PROFESSIONAL", "D_PRACTITIONER", "E_DISCOVERY"]
KnowledgeStatus = Literal["ESTABLISHED_CONTEXT", "HYPOTHESIS_ONLY"]


@dataclass(frozen=True)
class CrudeKnowledgeItem:
    id: str
    commodity: str
    family: str
    claim: str
    mechanism: str
    expected_effect: str
    conditions: tuple[str, ...]
    exceptions: tuple[str, ...]
    horizon: str
    source_name: str
    source_url: str
    source_tier: SourceTier
    status: KnowledgeStatus
    option_implication: str
    hypothesis_hook: str
    production_rule: bool = False


CRUDE_OIL_KNOWLEDGE_V1 = (
    CrudeKnowledgeItem(
        "CL_MCX_WTI_BENCHMARK",
        "CRUDE_OIL",
        "cross_market",
        "MCX Crude Oil and Crude Oil Mini are linked to the global WTI crude-oil benchmark.",
        "MCX identifies CME/NYMEX WTI crude oil as the underlying global benchmark for its crude-oil futures complex.",
        "Contemporaneous WTI direction and magnitude are a high-priority context input for MCX Crude Oil Mini, subject to local basis and currency effects.",
        ("WTI observation is available before the decision timestamp",),
        ("local basis can temporarily diverge", "currency can reinforce or offset the global move"),
        "intraday_to_multiday",
        "Multi Commodity Exchange of India",
        "https://www.mcxindia.com/products/energy/crude-oil",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Underlying direction is not sufficient for a long option; Option Brain must still evaluate expiry, IV, spread and premium translation.",
        "Test whether point-in-time WTI direction/magnitude improves CRUDEOILM direction and move-size forecasts beyond the local tape alone.",
    ),
    CrudeKnowledgeItem(
        "CL_MCX_FX_TRANSLATION",
        "CRUDE_OIL",
        "cross_market",
        "An INR-denominated MCX crude contract can respond differently from dollar-denominated WTI when USD/INR moves materially.",
        "The global crude benchmark is dollar-denominated while MCX contracts settle in rupees, so currency translation can reinforce or offset a global crude move.",
        "WTI and USD/INR alignment is a conditional prior for stronger MCX directional translation; opposition can weaken it.",
        ("WTI and USD/INR are contemporaneously observable",),
        ("basis/local positioning dominates briefly", "FX move is too small to matter at the trading horizon"),
        "intraday_to_multiday",
        "Multi Commodity Exchange of India",
        "https://www.mcxindia.com/products/energy/crude-oil",
        "A_PRIMARY",
        "HYPOTHESIS_ONLY",
        "Option Brain should estimate the expected MCX move after currency translation rather than using WTI direction mechanically.",
        "Test incremental CRUDEOILM forecast value from WTI plus USD/INR versus WTI alone and local-tape-only baselines.",
    ),
    CrudeKnowledgeItem(
        "CL_GLOBAL_SUPPLY_DEMAND",
        "CRUDE_OIL",
        "fundamentals",
        "Crude oil prices are shaped by global supply, demand, inventories and financial-market conditions.",
        "The oil market balances physical production and consumption through inventories and price changes, while expectations are transmitted through spot and futures markets.",
        "Unexpected tightening is a conditional bullish prior and unexpected loosening a conditional bearish prior; the effect must be conditioned on expectations and market reaction.",
        ("the information is new relative to market expectations", "materiality is credible"),
        ("already priced", "offsetting supply/demand shock", "headline lacks magnitude or timing"),
        "hours_to_months",
        "U.S. Energy Information Administration",
        "https://www.eia.gov/finance/markets/crudeoil/",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Fundamental direction can change both expected move and volatility; long-option economics still require separate validation.",
        "Classify supply/demand surprises and test direction, MFE/MAE and realized-volatility response conditional on pre-event regime.",
    ),
    CrudeKnowledgeItem(
        "CL_EIA_INVENTORY_BALANCE",
        "CRUDE_OIL",
        "inventories",
        "Petroleum inventories are a balancing point between oil supply and demand and are informative about market tightness.",
        "Stocks accumulate when production exceeds consumption and are drawn when consumption exceeds current supply; inventory changes also interact with expectations and futures spreads.",
        "A surprise crude draw is conditionally bullish and a surprise build conditionally bearish, but headline crude stocks alone are insufficient.",
        ("actual inventory data and a point-in-time expectation are both known",),
        ("gasoline/distillate/refinery data offset the crude headline", "imports/exports distort the weekly headline", "change was already expected"),
        "minutes_to_days",
        "U.S. Energy Information Administration",
        "https://www.eia.gov/finance/markets/crudeoil/balance.php",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Scheduled inventory releases can alter both direction and implied/realized volatility; Option Brain must account for event premium and post-release IV behavior.",
        "Measure actual-minus-expected crude, gasoline and distillate changes jointly and test their incremental predictive value after release time.",
    ),
    CrudeKnowledgeItem(
        "CL_REFINED_PRODUCTS_CONFIRMATION",
        "CRUDE_OIL",
        "inventories",
        "Gasoline and distillate inventories provide demand/refining context that can confirm or contradict the headline crude-stock change.",
        "Crude is transformed into refined products, so product stocks and seasonal demand help explain whether crude draws/builds reflect genuine end-demand, refinery flows or temporary logistics.",
        "Concordant crude/product surprises can strengthen a fundamental prior; contradictory product data should reduce confidence rather than force a direction.",
        ("product inventory data are available at the same point-in-time release",),
        ("seasonality dominates", "refinery maintenance distorts normal relationships"),
        "minutes_to_days",
        "U.S. Energy Information Administration",
        "https://www.eia.gov/finance/markets/products/balance.php",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Event-driven option decisions should consider the entire petroleum balance rather than the crude number in isolation.",
        "Test joint crude/gasoline/distillate surprise states against CRUDEOILM reaction, continuation, reversal and volatility.",
    ),
    CrudeKnowledgeItem(
        "CL_REFINERY_UTILIZATION",
        "CRUDE_OIL",
        "refining",
        "Refinery runs and utilization affect near-term crude demand and can change the interpretation of inventory movements.",
        "Higher refinery throughput raises crude inputs while maintenance/outages reduce crude intake; both can materially influence weekly stock changes.",
        "Rising utilization can be a crude-demand tailwind, while falling utilization can weaken crude demand, but interpretation depends on seasonality and outages.",
        ("utilization/refinery-input data are available before the decision",),
        ("planned seasonal maintenance", "product margins or outages dominate", "imports/exports overwhelm refinery-flow effect"),
        "days_to_weeks",
        "U.S. Energy Information Administration",
        "https://www.eia.gov/dnav/pet/pet_sum_sndw_dcus_nus_w.htm",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Refinery-state changes can alter expected crude direction and event volatility but should not directly choose a CE/PE.",
        "Condition inventory-surprise reactions on refinery utilization and crude-input changes.",
    ),
    CrudeKnowledgeItem(
        "CL_OPEC_SUPPLY_MANAGEMENT",
        "CRUDE_OIL",
        "supply",
        "OPEC and OPEC+ production targets and voluntary adjustments can materially affect expected global crude supply.",
        "Coordinated production increases, cuts, pauses or reversals alter expected barrels available to the global market and can change inventory trajectories.",
        "Unexpected effective tightening is a conditional bullish prior; unexpected easing is a conditional bearish prior.",
        ("decision is official", "implementation timing and magnitude are known", "surprise versus expectations can be estimated"),
        ("weak compliance", "compensation offsets headline change", "demand shock dominates", "decision was fully anticipated"),
        "hours_to_months",
        "Organization of the Petroleum Exporting Countries",
        "https://www.opec.org/",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "OPEC events can expand volatility; Option Brain must distinguish directional edge from event-volatility pricing.",
        "Track official production decisions, expected effective supply change, compliance/compensation and market reaction before promoting any OPEC rule.",
    ),
    CrudeKnowledgeItem(
        "CL_NON_OPEC_SUPPLY",
        "CRUDE_OIL",
        "supply",
        "Non-OPEC production changes are an important part of global crude supply and can offset or amplify OPEC actions.",
        "Independent producers add or remove supply according to economics, operational constraints and investment decisions rather than a single coordinated quota system.",
        "Unexpected non-OPEC supply growth is a conditional bearish prior and unexpected disruption/decline a conditional bullish prior.",
        ("material production information is new",),
        ("OPEC response offsets the change", "demand changes dominate", "reported change is temporary or already priced"),
        "days_to_months",
        "U.S. Energy Information Administration",
        "https://www.eia.gov/finance/markets/crudeoil/supply-nonopec.php",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Supply surprises can change move magnitude and volatility; use as context rather than a direct option trigger.",
        "Test production-change surprises conditional on OPEC policy, inventories and global-demand regime.",
    ),
    CrudeKnowledgeItem(
        "CL_GEOPOLITICAL_SUPPLY_RISK",
        "CRUDE_OIL",
        "geopolitics",
        "Geopolitical events matter most when they credibly threaten physical crude production, exports, transit or sanctions availability.",
        "A disruption or credible risk to barrels in production or transit can raise the scarcity/risk premium; diplomacy or restored flows can remove it.",
        "Confirmed material supply-risk escalation is a conditional bullish prior, while credible de-escalation/restoration is a conditional bearish prior.",
        ("event is new", "affected barrels or transit route are material", "source credibility is high"),
        ("no physical-flow consequence", "headline is repetitive", "market has already absorbed the event", "demand destruction offsets supply loss"),
        "minutes_to_weeks",
        "Multi Commodity Exchange of India",
        "https://www.mcxindia.com/products/energy/crude-oil",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Geopolitical events may inflate IV; correct crude direction can still be a poor long-premium trade if volatility is overpriced.",
        "Classify geopolitical events by first-detected time, novelty, affected flow, confirmation and market reaction instead of mapping keywords directly to CE/PE.",
    ),
    CrudeKnowledgeItem(
        "CL_WEATHER_DISRUPTION",
        "CRUDE_OIL",
        "weather",
        "Severe weather can affect crude production, shipping, refining and petroleum demand.",
        "Storms and extreme weather can interrupt offshore output, ports, pipelines or refineries and can also shift product demand.",
        "Weather impact is directionally ambiguous until the affected part of the supply chain and expected duration are known.",
        ("location, affected assets and timing are known",),
        ("refinery shutdown lowers crude demand while production remains intact", "impact is precautionary rather than realized"),
        "hours_to_weeks",
        "Multi Commodity Exchange of India",
        "https://www.mcxindia.com/products/energy/crude-oil",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Weather often raises uncertainty; Option Brain must model volatility separately from directional impact.",
        "Separate production, transport and refining disruption channels before testing weather-related CRUDEOILM behavior.",
    ),
    CrudeKnowledgeItem(
        "CL_CURVE_STRUCTURE",
        "CRUDE_OIL",
        "term_structure",
        "Crude futures-curve shape contains information about near-term physical tightness and storage economics.",
        "Backwardation typically accompanies tighter prompt supply/high convenience yield, while contango is commonly associated with looser supply and storage carry incentives.",
        "Stronger backwardation is a conditional tightness prior and stronger contango a conditional looseness prior; neither is an intraday direction signal by itself.",
        ("reliable multi-expiry futures prices are observable point-in-time",),
        ("financial flows distort spreads", "contract roll/microstructure effects", "temporary event premium"),
        "intraday_to_months",
        "CME Group",
        "https://www.cmegroup.com/insights/economic-research/2026/implications-of-wti-oil-futures-in-backwardation-amid-the-supply-crunch.html",
        "B_RESEARCH",
        "ESTABLISHED_CONTEXT",
        "Curve state may alter expected horizon and volatility; Option Brain should use it as context for expiry selection rather than a standalone trigger.",
        "Test whether prompt spreads/curve slope improve CRUDEOILM regime classification and continuation/reversal expectancy.",
    ),
    CrudeKnowledgeItem(
        "CL_CHINA_GLOBAL_DEMAND",
        "CRUDE_OIL",
        "demand",
        "Large changes in Chinese crude demand/imports can materially affect the global oil balance.",
        "China is a major crude importer, so changes in refining runs, imports and economic activity can alter marginal global demand.",
        "Unexpectedly stronger demand is a conditional bullish prior and weaker demand a conditional bearish prior.",
        ("data are new relative to expectations",),
        ("strategic stockpiling distorts apparent demand", "supply/geopolitical shock dominates", "data lag is too long for the horizon"),
        "days_to_months",
        "U.S. Energy Information Administration",
        "https://www.eia.gov/finance/markets/crudeoil/",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Demand repricing can affect both direction and volatility; option selection still requires separate premium economics.",
        "Test China demand/import/refining surprises as slow context rather than intraday standalone triggers.",
    ),
    CrudeKnowledgeItem(
        "CL_OPTION_VOL_SEPARATE",
        "CRUDE_OIL",
        "options_volatility",
        "Crude option-implied volatility is a separate state variable from the direction of the underlying crude market.",
        "Options price forward-looking uncertainty as well as direction through delta; event risk, skew, term structure and liquidity can change premium independently of the underlying thesis.",
        "A correct underlying-direction view does not guarantee a profitable long CE/PE.",
        ("reliable option quotes and IV are observable",),
        ("illiquid strikes or wide spreads distort inference",),
        "intraday_to_months",
        "CME Group",
        "https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.html",
        "A_PRIMARY",
        "ESTABLISHED_CONTEXT",
        "Option Brain must separately evaluate IV, skew, expiry, moneyness, liquidity and expected underlying move before buying premium.",
        "Measure option-premium translation separately from underlying Market-Brain accuracy whenever real point-in-time option data are available.",
    ),
)


def crude_oil_domain_knowledge_v1() -> dict:
    items = [asdict(item) for item in CRUDE_OIL_KNOWLEDGE_V1]
    return {
        "version": "CRUDE_OIL_DOMAIN_KNOWLEDGE_V1",
        "commodity": "CRUDE_OIL",
        "applies_to": ["CRUDEOILM", "CRUDEOIL"],
        "research_only": True,
        "production_rules_changed": False,
        "principle": "Domain knowledge supplies conditional priors and research hypotheses; point-in-time market evidence decides whether an edge is active.",
        "guardrails": {
            "knowledge_is_not_event_data": True,
            "knowledge_cannot_create_orders": True,
            "historical_news_requires_first_detected_at": True,
            "scheduled_events_require_point_in_time_expectation": True,
            "unknown_context_stays_unknown": True,
            "no_copper_threshold_transfer": True,
            "no_regular_crude_substitution_for_crude_oil_mini": True,
        },
        "source_policy": {
            "A_PRIMARY": "authoritative exchange/government/intergovernmental context",
            "B_RESEARCH": "research-supported context; validate empirically",
            "C_PROFESSIONAL": "professional literature; validate independently",
            "D_PRACTITIONER": "practitioner hypothesis; never promote without independent validation",
            "E_DISCOVERY": "discovery only; never decision evidence without corroboration",
        },
        "items": items,
        "option_objective": "Translate validated CRUDEOILM direction+magnitude+horizon+volatility into CE/PE/strike/expiry only when option economics are suitable.",
    }
