"""Conservative BTC macro-regime context from FRED/ALFRED daily vintages.

Daily FRED/ALFRED observations describe the background dollar/rates/risk regime.
They do NOT represent a timestamped intraday CPI/NFP/FOMC surprise. V1 therefore
keeps this evidence context-only and prevents it from supplying the second causal
origin required for a directional BTC thesis.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.crypto_market_intelligence import Evidence
from app.fred_btc_macro_regime_provider import FredBtcMacroRegimeCapture


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fred_macro_regime_context(
    capture: FredBtcMacroRegimeCapture,
    *,
    decision_at: datetime,
) -> Evidence:
    row = capture.validated()
    decision = _utc(decision_at)
    first_seen = _utc(row.first_seen_at)

    if row.historical_vintage_reconstruction:
        # ALFRED's calendar-date real-time period proves the state of a vintage,
        # but not the exact minute it became visible on that same date. A strict
        # prior calendar-day vintage is therefore required for historical clicks.
        if row.vintage_date >= decision.date():
            raise ValueError("historical FRED vintage does not prove same-day intraday visibility")
        visibility_basis = "PRIOR_CALENDAR_DAY_ALFRED_VINTAGE"
    else:
        if first_seen > decision:
            raise ValueError("live FRED macro regime was first seen after decision time")
        visibility_basis = "LIVE_ALPHAPILOT_FIRST_SEEN"

    usd = float(row.broad_usd_change_pct)
    real_yield = float(row.real_yield_change_bps)
    nasdaq = float(row.nasdaq_change_pct)
    vix = float(row.vix_change_pct)

    supportive_votes = sum((usd < 0, real_yield < 0, nasdaq > 0, vix < 0))
    restrictive_votes = sum((usd > 0, real_yield > 0, nasdaq < 0, vix > 0))
    if supportive_votes >= 3 and restrictive_votes == 0:
        regime = "RISK_LIQUIDITY_SUPPORTIVE"
    elif restrictive_votes >= 3 and supportive_votes == 0:
        regime = "RISK_LIQUIDITY_RESTRICTIVE"
    else:
        regime = "MIXED_MACRO_REGIME"

    confidence = 0.75 if max(supportive_votes, restrictive_votes) == 4 else 0.65 if max(supportive_votes, restrictive_votes) == 3 else 0.5
    reason = (
        "Daily FRED/ALFRED dollar, real-yield, Nasdaq and VIX changes align as a supportive background risk/liquidity regime; this is context only, not an intraday macro-event vote."
        if regime == "RISK_LIQUIDITY_SUPPORTIVE"
        else "Daily FRED/ALFRED dollar, real-yield, Nasdaq and VIX changes align as a restrictive background risk/liquidity regime; this is context only, not an intraday macro-event vote."
        if regime == "RISK_LIQUIDITY_RESTRICTIVE"
        else "Daily FRED/ALFRED cross-asset changes are mixed and remain background macro context only."
    )

    # For a historical prior-day vintage, evidence is assembled at the click from
    # information conservatively known before that calendar day. For a live fetch,
    # AlphaPilot's actual first_seen timestamp is the evidence availability time.
    observed_at = decision if row.historical_vintage_reconstruction else first_seen
    return Evidence(
        family="BTC_MACRO_CROSS_ASSET",
        causal_origin="GLOBAL_RISK_LIQUIDITY_REGIME",
        stance="UNKNOWN",
        strength="MEDIUM" if regime != "MIXED_MACRO_REGIME" else "LOW",
        confidence=confidence,
        observed_at=observed_at,
        reason=reason,
        context_only=True,
        source="FRED_ALFRED_DAILY_REGIME",
        metadata={
            "regime": regime,
            "supportive_votes": supportive_votes,
            "restrictive_votes": restrictive_votes,
            "broad_usd_series": row.broad_usd.series_id,
            "broad_usd_change_pct": usd,
            "real_yield_series": row.real_yield_10y.series_id,
            "real_yield_change_bps": real_yield,
            "nasdaq_series": row.nasdaq_composite.series_id,
            "nasdaq_change_pct": nasdaq,
            "vix_series": row.vix.series_id,
            "vix_change_pct": vix,
            "vintage_date": row.vintage_date.isoformat(),
            "first_seen_at": first_seen.isoformat(),
            "historical_vintage_reconstruction": row.historical_vintage_reconstruction,
            "exact_intraday_availability_proven": row.exact_intraday_availability_proven,
            "visibility_basis": visibility_basis,
            "daily_regime_not_intraday_event": True,
            "standalone_direction_allowed": False,
            "may_supply_second_intraday_causal_origin": False,
            "btc_direction_generated": False,
            "options_trade_generated": False,
            "futures_trade_generated": False,
        },
    )


def architecture_contract() -> dict:
    return {
        "version": "FRED_BTC_MACRO_REGIME_CONTEXT_V1",
        "daily_regime_context_only": True,
        "daily_regime_may_create_btc_direction": False,
        "daily_regime_may_supply_second_intraday_origin": False,
        "historical_same_day_intraday_vintage_allowed": False,
        "live_same_day_first_seen_required": True,
        "broad_usd_is_not_labeled_dxy": True,
        "exact_macro_event_surprise_is_separate_lane": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
