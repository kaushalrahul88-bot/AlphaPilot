"""Prior-normalized macro-event semantics and market confirmation for BTC.

Only CPI and Employment Situation numeric surprises are interpreted in V1.
Raw differences never use fixed economic-unit thresholds. Each metric is ranked
against strictly prior, already-valid surprises of the same event type. At least
20 prior samples are required by default.

A hawkish/dovish semantic state becomes one MACRO_EVENT_SHOCK directional origin
only when contemporaneous BTC and at least two independent cross-asset reactions
confirm the expected risk direction. This module never emits an Options or
Futures trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.crypto_macro_event_intelligence import MacroNumericSurprise
from app.crypto_market_intelligence import Evidence

MacroSemanticState = Literal[
    "HAWKISH_SHOCK",
    "DOVISH_SHOCK",
    "MIXED_OR_AMBIGUOUS",
    "INSUFFICIENT_PRIOR_HISTORY",
    "UNSUPPORTED_EVENT_TYPE",
]


def _utc_exact(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _bounded(value: float, *, name: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return number


def _empirical_percentile(value: float, prior: list[float]) -> float:
    if not prior:
        raise ValueError("prior sample is required")
    return sum(1 for item in prior if float(item) <= float(value)) / len(prior)


@dataclass(frozen=True)
class NormalizedMacroSurprise:
    event_key: str
    event_type: str
    release_at: datetime
    semantic_state: MacroSemanticState
    metric_percentiles: dict[str, float]
    prior_sample_count: int
    lower_percentile: float
    upper_percentile: float
    direction: str = "UNKNOWN"
    standalone_direction_allowed: bool = False

    def validated(self) -> "NormalizedMacroSurprise":
        _utc_exact(self.release_at, name="release_at")
        if int(self.prior_sample_count) < 0:
            raise ValueError("prior_sample_count must be >= 0")
        lower = _bounded(self.lower_percentile, name="lower_percentile")
        upper = _bounded(self.upper_percentile, name="upper_percentile")
        if not lower < upper:
            raise ValueError("lower_percentile must be < upper_percentile")
        for metric, value in self.metric_percentiles.items():
            _bounded(value, name=f"metric_percentiles[{metric!r}]")
        if self.direction != "UNKNOWN" or self.standalone_direction_allowed:
            raise ValueError("normalized macro surprise alone cannot assign BTC direction")
        return self


@dataclass(frozen=True)
class MacroMarketReaction:
    event_key: str
    release_at: datetime
    observed_at: datetime
    first_seen_at: datetime
    btc_return_pct: float
    nasdaq_return_pct: float | None
    broad_usd_return_pct: float | None
    real_yield_change_bps: float | None
    btc_abs_move_percentile: float
    source: str
    source_verified: bool = True

    def validated(self) -> "MacroMarketReaction":
        release = _utc_exact(self.release_at, name="release_at")
        observed = _utc_exact(self.observed_at, name="observed_at")
        first_seen = _utc_exact(self.first_seen_at, name="first_seen_at")
        if observed < release:
            raise ValueError("macro market reaction cannot end before event release")
        if first_seen < observed:
            raise ValueError("market reaction first_seen_at cannot precede its completed observation window")
        _bounded(self.btc_abs_move_percentile, name="btc_abs_move_percentile")
        if not str(self.source or "").strip() or self.source_verified is not True:
            raise ValueError("macro market reaction requires verified source")
        for name, value in (
            ("btc_return_pct", self.btc_return_pct),
            ("nasdaq_return_pct", self.nasdaq_return_pct),
            ("broad_usd_return_pct", self.broad_usd_return_pct),
            ("real_yield_change_bps", self.real_yield_change_bps),
        ):
            if value is not None:
                float(value)
        return self


def normalize_macro_surprise(
    current: MacroNumericSurprise,
    prior_surprises: list[MacroNumericSurprise],
    *,
    min_prior_samples: int = 20,
    lower_percentile: float = 0.20,
    upper_percentile: float = 0.80,
) -> NormalizedMacroSurprise:
    current.validated()
    if int(min_prior_samples) < 5:
        raise ValueError("min_prior_samples must be >= 5")
    lower = _bounded(lower_percentile, name="lower_percentile")
    upper = _bounded(upper_percentile, name="upper_percentile")
    if lower >= upper:
        raise ValueError("lower_percentile must be < upper_percentile")

    current_release = _utc_exact(current.release_at, name="current release_at")
    eligible = [
        row.validated() for row in prior_surprises
        if row.event_type == current.event_type
        and row.event_key != current.event_key
        and _utc_exact(row.release_at, name="prior release_at") < current_release
    ]
    required_metrics: tuple[str, ...]
    if current.event_type == "CPI":
        required_metrics = ("headline_mom_pct", "core_mom_pct")
    elif current.event_type == "EMPLOYMENT_SITUATION":
        required_metrics = ("payroll_change_k", "unemployment_rate_pct", "avg_hourly_earnings_mom_pct")
    else:
        return NormalizedMacroSurprise(
            event_key=current.event_key,
            event_type=current.event_type,
            release_at=current_release,
            semantic_state="UNSUPPORTED_EVENT_TYPE",
            metric_percentiles={},
            prior_sample_count=len(eligible),
            lower_percentile=lower,
            upper_percentile=upper,
        ).validated()

    if any(metric not in current.surprise for metric in required_metrics):
        return NormalizedMacroSurprise(
            event_key=current.event_key,
            event_type=current.event_type,
            release_at=current_release,
            semantic_state="MIXED_OR_AMBIGUOUS",
            metric_percentiles={},
            prior_sample_count=len(eligible),
            lower_percentile=lower,
            upper_percentile=upper,
        ).validated()

    complete_prior = [row for row in eligible if all(metric in row.surprise for metric in required_metrics)]
    if len(complete_prior) < int(min_prior_samples):
        return NormalizedMacroSurprise(
            event_key=current.event_key,
            event_type=current.event_type,
            release_at=current_release,
            semantic_state="INSUFFICIENT_PRIOR_HISTORY",
            metric_percentiles={},
            prior_sample_count=len(complete_prior),
            lower_percentile=lower,
            upper_percentile=upper,
        ).validated()

    percentiles = {
        metric: _empirical_percentile(
            float(current.surprise[metric]),
            [float(row.surprise[metric]) for row in complete_prior],
        )
        for metric in required_metrics
    }

    semantic: MacroSemanticState = "MIXED_OR_AMBIGUOUS"
    if current.event_type == "CPI":
        headline = percentiles["headline_mom_pct"]
        core = percentiles["core_mom_pct"]
        if headline >= upper and core >= upper:
            semantic = "HAWKISH_SHOCK"
        elif headline <= lower and core <= lower:
            semantic = "DOVISH_SHOCK"
    else:
        hawkish = sum((
            percentiles["payroll_change_k"] >= upper,
            percentiles["unemployment_rate_pct"] <= lower,
            percentiles["avg_hourly_earnings_mom_pct"] >= upper,
        ))
        dovish = sum((
            percentiles["payroll_change_k"] <= lower,
            percentiles["unemployment_rate_pct"] >= upper,
            percentiles["avg_hourly_earnings_mom_pct"] <= lower,
        ))
        if hawkish >= 2 and dovish == 0:
            semantic = "HAWKISH_SHOCK"
        elif dovish >= 2 and hawkish == 0:
            semantic = "DOVISH_SHOCK"

    return NormalizedMacroSurprise(
        event_key=current.event_key,
        event_type=current.event_type,
        release_at=current_release,
        semantic_state=semantic,
        metric_percentiles=percentiles,
        prior_sample_count=len(complete_prior),
        lower_percentile=lower,
        upper_percentile=upper,
    ).validated()


def macro_event_evidence(
    normalized: NormalizedMacroSurprise,
    reaction: MacroMarketReaction,
    *,
    decision_at: datetime,
    max_reaction_minutes: int = 30,
    min_btc_abs_move_percentile: float = 0.70,
) -> Evidence:
    state = normalized.validated()
    market = reaction.validated()
    decision = _utc_exact(decision_at, name="decision_at")
    if state.event_key != market.event_key or _utc_exact(state.release_at, name="state release_at") != _utc_exact(market.release_at, name="reaction release_at"):
        raise ValueError("macro semantic state and market reaction must refer to same event")
    if _utc_exact(market.first_seen_at, name="reaction first_seen_at") > decision:
        raise ValueError("macro market reaction was not yet visible by decision time")
    lag_seconds = (_utc_exact(market.observed_at, name="reaction observed_at") - _utc_exact(state.release_at, name="state release_at")).total_seconds()
    if lag_seconds < 0 or lag_seconds > int(max_reaction_minutes) * 60:
        return _unknown_evidence(state, market, decision, "Market reaction window is outside allowed post-release horizon.")
    move_percentile = _bounded(min_btc_abs_move_percentile, name="min_btc_abs_move_percentile")
    if market.btc_abs_move_percentile < move_percentile:
        return _unknown_evidence(state, market, decision, "BTC event-window move is not unusually large versus prior event windows.")
    if state.semantic_state not in {"HAWKISH_SHOCK", "DOVISH_SHOCK"}:
        return _unknown_evidence(state, market, decision, "Macro surprise is not a coherent prior-normalized hawkish/dovish shock.")

    bullish = state.semantic_state == "DOVISH_SHOCK"
    btc_aligned = market.btc_return_pct > 0 if bullish else market.btc_return_pct < 0
    alignments = []
    if market.nasdaq_return_pct is not None:
        alignments.append(market.nasdaq_return_pct > 0 if bullish else market.nasdaq_return_pct < 0)
    if market.broad_usd_return_pct is not None:
        alignments.append(market.broad_usd_return_pct < 0 if bullish else market.broad_usd_return_pct > 0)
    if market.real_yield_change_bps is not None:
        alignments.append(market.real_yield_change_bps < 0 if bullish else market.real_yield_change_bps > 0)
    cross_asset_aligned = sum(bool(value) for value in alignments)
    if not btc_aligned or cross_asset_aligned < 2:
        return _unknown_evidence(state, market, decision, "Official macro shock lacks aligned BTC plus two-source cross-asset confirmation.")

    stance = "BULLISH" if bullish else "BEARISH"
    return Evidence(
        family="MACRO_CROSS_ASSET",
        causal_origin="MACRO_EVENT_SHOCK",
        stance=stance,
        strength="HIGH" if cross_asset_aligned == 3 else "MEDIUM",
        confidence=0.85 if cross_asset_aligned == 3 else 0.78,
        observed_at=_utc_exact(market.first_seen_at, name="market first_seen_at"),
        reason=(
            "Prior-normalized dovish official macro shock is confirmed by BTC and cross-asset risk reaction."
            if bullish else
            "Prior-normalized hawkish official macro shock is confirmed by BTC and cross-asset risk reaction."
        ),
        context_only=False,
        source="OFFICIAL_MACRO_PLUS_VERIFIED_MARKET_REACTION",
        metadata={
            "event_key": state.event_key,
            "event_type": state.event_type,
            "semantic_state": state.semantic_state,
            "metric_percentiles": dict(state.metric_percentiles),
            "prior_sample_count": state.prior_sample_count,
            "btc_abs_move_percentile": market.btc_abs_move_percentile,
            "btc_reaction_aligned": btc_aligned,
            "cross_asset_alignment_count": cross_asset_aligned,
            "reaction_lag_seconds": lag_seconds,
            "standalone_macro_surprise_direction_allowed": False,
            "market_confirmation_required": True,
            "may_inform_options": True,
            "may_generate_options_trade": False,
            "may_generate_futures_trade": False,
        },
    )


def _unknown_evidence(
    state: NormalizedMacroSurprise,
    market: MacroMarketReaction,
    decision_at: datetime,
    reason: str,
) -> Evidence:
    return Evidence(
        family="MACRO_CROSS_ASSET",
        causal_origin="MACRO_EVENT_SHOCK",
        stance="UNKNOWN",
        strength="LOW",
        confidence=0.5,
        observed_at=min(_utc_exact(market.first_seen_at, name="market first_seen_at"), decision_at),
        reason=reason,
        context_only=True,
        source="OFFICIAL_MACRO_PLUS_VERIFIED_MARKET_REACTION",
        metadata={
            "event_key": state.event_key,
            "event_type": state.event_type,
            "semantic_state": state.semantic_state,
            "metric_percentiles": dict(state.metric_percentiles),
            "prior_sample_count": state.prior_sample_count,
            "btc_abs_move_percentile": market.btc_abs_move_percentile,
            "standalone_macro_surprise_direction_allowed": False,
            "market_confirmation_required": True,
            "may_generate_options_trade": False,
            "may_generate_futures_trade": False,
        },
    )


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_MACRO_EVENT_SEMANTICS_V1",
        "supported_directional_events": ["CPI", "EMPLOYMENT_SITUATION"],
        "fixed_raw_surprise_thresholds_used": False,
        "strictly_prior_surprise_distribution_required": True,
        "default_min_prior_samples": 20,
        "cpi_headline_and_core_alignment_required": True,
        "employment_multi_metric_alignment_required": True,
        "fomc_numeric_or_text_direction_supported": False,
        "btc_market_confirmation_required": True,
        "minimum_cross_asset_confirmations": 2,
        "btc_event_move_prior_percentile_required": True,
        "macro_event_can_be_one_independent_causal_origin": True,
        "macro_event_directly_generates_options_trade": False,
        "macro_event_directly_generates_futures_trade": False,
        "research_only": True,
    }
