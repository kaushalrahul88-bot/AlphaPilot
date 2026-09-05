"""Assemble and prior-normalize exact macro market reaction windows.

Raw reaction windows are built from reconstructible completed CoinDCX BTC spot
1-minute candles plus the replay-only Massive CME futures reaction provider.
The provider layer supplies raw returns only. BTC move magnitude is normalized
here against strictly earlier observations of the same macro event type, same
window length, and same BTC source.

This separation prevents a provider from smuggling future distribution
information into an event classifier. At least 20 prior reaction windows are
required by default. No missing reaction is converted to a neutral vote and no
Options/Futures trade is generated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Literal

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow
from app.crypto_macro_event_semantics import MacroMarketReaction
from app.massive_macro_futures_reaction_provider import MassiveMacroFuturesReaction

MacroEventType = Literal["CPI", "EMPLOYMENT_SITUATION"]
BTC_SOURCE = "COINDCX_PUBLIC_SPOT_CANDLES"
USD_PROXY_KIND = "INVERSE_EURUSD_FUTURES_6E"


def _utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: float, *, name: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _percentile(value: float, prior: list[float]) -> float:
    if not prior:
        raise ValueError("prior reaction sample is required")
    current = abs(_finite(value, name="current BTC return"))
    normalized = [abs(_finite(item, name="prior BTC return")) for item in prior]
    return sum(1 for item in normalized if item <= current) / len(normalized)


@dataclass(frozen=True)
class RawMacroMarketReaction:
    event_key: str
    event_type: MacroEventType
    release_at: datetime
    observed_at: datetime
    reconstructible_available_at: datetime
    window_minutes: int
    btc_return_pct: float
    nasdaq_return_pct: float
    usd_strength_proxy_return_pct: float
    btc_source: str
    cross_asset_source: str
    usd_strength_proxy_kind: str = USD_PROXY_KIND
    reconstructible_history: bool = True
    prospective_live_availability_proven: bool = False

    def validated(self) -> "RawMacroMarketReaction":
        release = _utc(self.release_at, name="release_at")
        observed = _utc(self.observed_at, name="observed_at")
        available = _utc(self.reconstructible_available_at, name="reconstructible_available_at")
        if self.event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("unsupported macro event_type")
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if int(self.window_minutes) <= 0 or int(self.window_minutes) > 30:
            raise ValueError("window_minutes must be between 1 and 30")
        if observed <= release:
            raise ValueError("observed_at must be after release_at")
        if int((observed - release).total_seconds()) != int(self.window_minutes) * 60:
            raise ValueError("observed_at must equal release_at plus window_minutes")
        if available < observed:
            raise ValueError("reconstructible availability cannot precede completed reaction window")
        for name in ("btc_return_pct", "nasdaq_return_pct", "usd_strength_proxy_return_pct"):
            _finite(getattr(self, name), name=name)
        if self.btc_source != BTC_SOURCE:
            raise ValueError("raw macro reaction requires CoinDCX public BTC spot candles")
        if not str(self.cross_asset_source or "").strip():
            raise ValueError("cross_asset_source is required")
        if self.usd_strength_proxy_kind != USD_PROXY_KIND:
            raise ValueError("unsupported USD-strength proxy kind")
        if self.reconstructible_history is not True or self.prospective_live_availability_proven is not False:
            raise ValueError("V1 raw macro reaction is historical/replay reconstruction only")
        return self


def _exact_candle(
    candles: list[BtcSpotCandleArchiveRow],
    *,
    close_at: datetime,
) -> BtcSpotCandleArchiveRow:
    target = _utc(close_at, name="target candle close_at")
    matches = [row.validated() for row in candles if _utc(row.close_at, name="candle close_at") == target]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one CoinDCX BTC candle closing at {target.isoformat()}; found {len(matches)}")
    row = matches[0]
    if _utc(row.available_at, name="candle available_at") < target:
        raise ValueError("BTC candle cannot be available before completion")
    return row


def assemble_raw_macro_reaction(
    *,
    event_key: str,
    event_type: MacroEventType,
    release_at: datetime,
    btc_candles: list[BtcSpotCandleArchiveRow],
    cross_asset_reaction: MassiveMacroFuturesReaction,
) -> RawMacroMarketReaction:
    release = _utc(release_at, name="release_at")
    cross = cross_asset_reaction.validated()
    if cross.event_key != event_key or cross.event_type != event_type:
        raise ValueError("BTC and cross-asset reaction inputs must refer to the same macro event")
    if _utc(cross.release_at, name="cross release_at") != release:
        raise ValueError("BTC and cross-asset reaction release timestamps must match exactly")
    observed = _utc(cross.observed_at, name="cross observed_at")
    window_seconds = int((observed - release).total_seconds())
    if window_seconds <= 0 or window_seconds % 60:
        raise ValueError("cross-asset reaction window must be whole positive minutes")
    window_minutes = window_seconds // 60

    pre = _exact_candle(btc_candles, close_at=release)
    post = _exact_candle(btc_candles, close_at=observed)
    if pre.provenance.point_in_time_proven is not True or post.provenance.point_in_time_proven is not True:
        raise ValueError("BTC reaction candles must be point-in-time proven")
    if pre.provenance.reconstructible_public_data is not True or post.provenance.reconstructible_public_data is not True:
        raise ValueError("BTC reaction V1 requires reconstructible public candles")
    if pre.provenance.provider != "COINDCX" or post.provenance.provider != "COINDCX":
        raise ValueError("BTC reaction candles must come from CoinDCX")

    btc_return = (float(post.close) / float(pre.close) - 1.0) * 100.0
    available = max(
        _utc(post.available_at, name="post BTC available_at"),
        _utc(cross.reconstructible_available_at, name="cross available_at"),
    )
    return RawMacroMarketReaction(
        event_key=event_key,
        event_type=event_type,
        release_at=release,
        observed_at=observed,
        reconstructible_available_at=available,
        window_minutes=window_minutes,
        btc_return_pct=btc_return,
        nasdaq_return_pct=float(cross.nasdaq_futures_return_pct),
        usd_strength_proxy_return_pct=float(cross.usd_strength_proxy_return_pct),
        btc_source=BTC_SOURCE,
        cross_asset_source=cross.provider,
    ).validated()


def normalize_macro_market_reaction(
    current: RawMacroMarketReaction,
    prior_reactions: list[RawMacroMarketReaction],
    *,
    min_prior_events: int = 20,
) -> MacroMarketReaction:
    row = current.validated()
    if int(min_prior_events) < 5:
        raise ValueError("min_prior_events must be >= 5")
    current_release = _utc(row.release_at, name="current release_at")
    eligible: list[RawMacroMarketReaction] = []
    for candidate in prior_reactions:
        prior = candidate.validated()
        prior_release = _utc(prior.release_at, name="prior release_at")
        prior_available = _utc(prior.reconstructible_available_at, name="prior available_at")
        if prior.event_type != row.event_type:
            continue
        if prior.event_key == row.event_key:
            continue
        if prior.window_minutes != row.window_minutes:
            continue
        if prior.btc_source != row.btc_source:
            continue
        if prior_release >= current_release or prior_available >= current_release:
            continue
        eligible.append(prior)
    if len(eligible) < int(min_prior_events):
        raise ValueError(
            f"insufficient strictly prior comparable BTC reaction history: {len(eligible)} < {int(min_prior_events)}"
        )

    percentile = _percentile(row.btc_return_pct, [prior.btc_return_pct for prior in eligible])
    return MacroMarketReaction(
        event_key=row.event_key,
        release_at=row.release_at,
        observed_at=row.observed_at,
        first_seen_at=row.reconstructible_available_at,
        btc_return_pct=row.btc_return_pct,
        nasdaq_return_pct=row.nasdaq_return_pct,
        broad_usd_return_pct=None,
        real_yield_change_bps=None,
        btc_abs_move_percentile=percentile,
        source="COINDCX_SPOT_PLUS_MASSIVE_CME_FUTURES_RECONSTRUCTED",
        source_verified=True,
        usd_strength_proxy_return_pct=row.usd_strength_proxy_return_pct,
        usd_strength_proxy_kind=row.usd_strength_proxy_kind,
    ).validated()


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_MACRO_MARKET_REACTION_V1",
        "btc_source": BTC_SOURCE,
        "btc_exact_pre_release_completed_bar_required": True,
        "btc_exact_reaction_window_completed_bar_required": True,
        "cross_asset_source": "MASSIVE_CME_FUTURES",
        "nasdaq_dimension": "NQ_FUTURES",
        "usd_dimension": USD_PROXY_KIND,
        "usd_proxy_claimed_to_be_dxy": False,
        "provider_supplies_btc_move_percentile": False,
        "strictly_prior_btc_move_distribution_required": True,
        "same_event_type_required_for_btc_move_percentile": True,
        "same_window_required_for_btc_move_percentile": True,
        "same_btc_source_required_for_btc_move_percentile": True,
        "default_min_prior_events": 20,
        "future_or_unresolved_prior_reaction_may_enter_distribution": False,
        "missing_reaction_treated_as_neutral": False,
        "historical_replay_reconstruction_supported": True,
        "prospective_live_confirmation_enabled": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
