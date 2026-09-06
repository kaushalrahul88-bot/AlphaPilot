"""Explicit server-time BTC Options live shadow click.

One invocation captures a fresh Delta India public option-chain snapshot and a
completed Delta BTC perpetual OI context window before assigning the decision
time. The BTC underlying thesis is then frozen using only information available
by that server-side time. A real Options shadow entry is admitted only when the
underlying thesis is BULLISH/BEARISH and an exact fresh Delta bid/ask quote passes
a frozen deterministic candidate-validation policy. UNKNOWN is NO TRADE.

Delta remains a candidate Options venue. Perpetual OI is context for the shared
underlying thesis only; it can never generate a Futures trade or substitute for
an Options quote/fill.

Research/shadow only. No account access, order placement, execution, or capital.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Any, Callable

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.crypto_btc_delta_options_probe_postgres import PostgresDeltaIndiaOptionsProbeStore
from app.crypto_btc_live_shadow_click_postgres import PostgresBtcLiveShadowClickStore
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_btc_prospective_proof_bridge import freeze_prospective_btc_thesis_from_existing_sources
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_prospective_thesis_postgres import PostgresProspectiveBtcThesisTapeStore
from app.delta_india_btc_derivatives_context import (
    DeltaIndiaBtcDerivativesContextPolicy,
    DeltaIndiaBtcDerivativesPublicProvider,
)
from app.delta_india_btc_options_public_provider import DeltaIndiaBtcOptionsPublicProvider, DeltaIndiaOptionsProbePolicy

# Delta India documents BTC/ETH Options expiry at 5:30 PM IST. IST is UTC+05:30,
# therefore the exchange expiry instant is 12:00 UTC on the encoded expiry date.
DELTA_BTC_OPTIONS_EXPIRY_UTC = time(hour=12, minute=0, tzinfo=timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _expiry_at(value: Any) -> datetime:
    parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
    return datetime.combine(parsed, DELTA_BTC_OPTIONS_EXPIRY_UTC)


def _finite_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


@dataclass(frozen=True)
class LiveShadowOptionSelectionPolicy:
    """Frozen candidate-only policy; thresholds are outcome-blind."""

    max_quote_age_seconds: int = 120
    target_abs_delta: float = 0.50
    min_abs_delta: float = 0.20
    max_abs_delta: float = 0.85
    max_relative_spread_pct: float = 8.0
    min_open_interest: float = 1.0
    min_volume: float = 1.0
    min_expiry_buffer_hours: float = 2.0
    min_expiry_holding_multiple: float = 1.50

    def validated(self) -> "LiveShadowOptionSelectionPolicy":
        if int(self.max_quote_age_seconds) < 0:
            raise ValueError("max_quote_age_seconds must be >= 0")
        for name in (
            "target_abs_delta",
            "min_abs_delta",
            "max_abs_delta",
            "max_relative_spread_pct",
            "min_open_interest",
            "min_volume",
            "min_expiry_buffer_hours",
            "min_expiry_holding_multiple",
        ):
            value = float(getattr(self, name))
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not 0 < self.min_abs_delta <= self.target_abs_delta <= self.max_abs_delta <= 1:
            raise ValueError("delta selection range must satisfy 0 < min <= target <= max <= 1")
        if self.max_relative_spread_pct <= 0:
            raise ValueError("max_relative_spread_pct must be > 0")
        if self.min_expiry_holding_multiple <= 0:
            raise ValueError("min_expiry_holding_multiple must be > 0")
        return self

    def frozen_dict(self) -> dict:
        self.validated()
        return {
            "version": "BTC_LIVE_SHADOW_OPTION_SELECTION_V2",
            "venue_state": "DELTA_CANDIDATE_ONLY",
            "expiry_rule": "DELTA_BTC_OPTIONS_EXPIRE_17_30_IST_AND_MUST_COVER_HOLDING_HORIZON",
            "side_rule": "BULLISH_BUY_CALL_BEARISH_BUY_PUT_UNKNOWN_NO_TRADE",
            "fill_rule": "OBSERVED_BEST_ASK",
            "exit_rule": "OBSERVED_EXACT_CONTRACT_BEST_BID_ONLY",
            "max_quote_age_seconds": int(self.max_quote_age_seconds),
            "target_abs_delta": float(self.target_abs_delta),
            "min_abs_delta": float(self.min_abs_delta),
            "max_abs_delta": float(self.max_abs_delta),
            "max_relative_spread_pct": float(self.max_relative_spread_pct),
            "min_open_interest": float(self.min_open_interest),
            "min_volume": float(self.min_volume),
            "min_expiry_buffer_hours": float(self.min_expiry_buffer_hours),
            "min_expiry_holding_multiple": float(self.min_expiry_holding_multiple),
            "greeks_required": ["delta", "gamma", "theta", "vega"],
            "model_price_substitution": False,
            "mark_price_as_fill": False,
            "futures_quote_substitution": False,
        }


def select_delta_option_for_shadow_entry(
    snapshot: dict,
    *,
    market_direction: str,
    decision_at: datetime,
    expected_holding_hours: float,
    policy: LiveShadowOptionSelectionPolicy | None = None,
) -> dict:
    policy = (policy or LiveShadowOptionSelectionPolicy()).validated()
    direction = str(market_direction or "UNKNOWN").upper()
    decision = _utc(decision_at)
    holding_hours = float(expected_holding_hours)
    if not isfinite(holding_hours) or holding_hours <= 0:
        raise ValueError("expected_holding_hours must be finite and > 0")
    if direction == "UNKNOWN":
        return {"status": "NO_TRADE", "reason": "UNDERLYING_THESIS_UNKNOWN", "option_entry": None}
    if direction not in {"BULLISH", "BEARISH"}:
        raise ValueError("market_direction must be BULLISH, BEARISH, or UNKNOWN")
    if snapshot.get("venue") != "DELTA_EXCHANGE_INDIA" or snapshot.get("candidate_only") is not True:
        raise ValueError("live shadow entry requires candidate-only Delta India snapshot")
    if snapshot.get("execution_enabled") is not False or snapshot.get("trading_auth_used") is not False:
        raise ValueError("Delta shadow entry snapshot must be public market data with execution disabled")

    seen = _stamp(snapshot.get("first_seen_at"))
    age = (decision - seen).total_seconds()
    if age < 0:
        raise ValueError("Delta option snapshot was first seen after decision_at")
    if age > int(policy.max_quote_age_seconds):
        return {"status": "NO_TRADE", "reason": "DELTA_OPTION_SNAPSHOT_STALE", "option_entry": None}

    expiry_at = _expiry_at(snapshot.get("nearest_expiry"))
    expiry_hours = (expiry_at - decision).total_seconds() / 3600.0
    required_expiry_hours = max(
        float(policy.min_expiry_buffer_hours),
        holding_hours * float(policy.min_expiry_holding_multiple),
    )
    if expiry_hours <= required_expiry_hours:
        return {
            "status": "NO_TRADE",
            "reason": "DELTA_NEAREST_EXPIRY_TOO_CLOSE_FOR_FROZEN_HORIZON",
            "option_entry": None,
            "expiry_at": expiry_at.isoformat(),
            "expiry_hours": expiry_hours,
            "required_expiry_hours": required_expiry_hours,
        }

    required_type = "CALL" if direction == "BULLISH" else "PUT"
    reference_spot = float(snapshot.get("reference_spot_price"))
    if not isfinite(reference_spot) or reference_spot <= 0:
        raise ValueError("Delta snapshot reference_spot_price must be finite and > 0")

    eligible: list[tuple[tuple[float, float, float, float, str], dict]] = []
    for raw in snapshot.get("quotes") or []:
        if not isinstance(raw, dict) or str(raw.get("option_type") or "") != required_type:
            continue
        bid = _finite_number(raw.get("best_bid"))
        ask = _finite_number(raw.get("best_ask"))
        strike = _finite_number(raw.get("strike_price"))
        greeks = raw.get("greeks") if isinstance(raw.get("greeks"), dict) else {}
        delta = _finite_number(greeks.get("delta"))
        gamma = _finite_number(greeks.get("gamma"))
        theta = _finite_number(greeks.get("theta"))
        vega = _finite_number(greeks.get("vega"))
        oi = _finite_number(raw.get("open_interest"))
        volume = _finite_number(raw.get("volume"))
        bid_iv = _finite_number(raw.get("bid_iv"))
        ask_iv = _finite_number(raw.get("ask_iv"))

        if bid is None or ask is None or strike is None or delta is None:
            continue
        if bid <= 0 or ask <= 0 or ask < bid or strike <= 0:
            continue
        if required_type == "CALL" and delta <= 0:
            continue
        if required_type == "PUT" and delta >= 0:
            continue
        abs_delta = abs(delta)
        if not policy.min_abs_delta <= abs_delta <= policy.max_abs_delta:
            continue
        if gamma is None or gamma < 0 or theta is None or vega is None or vega < 0:
            continue
        if oi is None or oi < policy.min_open_interest:
            continue
        if volume is None or volume < policy.min_volume:
            continue
        if bid_iv is None or ask_iv is None or bid_iv <= 0 or ask_iv <= 0:
            continue

        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid * 100.0
        if spread_pct > policy.max_relative_spread_pct:
            continue
        rank = (
            abs(abs_delta - policy.target_abs_delta),
            spread_pct,
            -oi,
            abs(strike - reference_spot),
            str(raw.get("symbol") or ""),
        )
        eligible.append((rank, raw))

    if not eligible:
        return {"status": "NO_TRADE", "reason": "NO_ADMISSIBLE_FRESH_DELTA_OPTION_QUOTE", "option_entry": None}

    eligible.sort(key=lambda item: item[0])
    raw = eligible[0][1]
    bid = float(raw["best_bid"])
    ask = float(raw["best_ask"])
    mid = (bid + ask) / 2.0
    entry = {
        "venue": "DELTA_EXCHANGE_INDIA",
        "candidate_only": True,
        "instrument_type": "OPTIONS",
        "action": "BUY",
        "option_type": required_type,
        "symbol": str(raw["symbol"]),
        "product_id": raw.get("product_id"),
        "expiry_date": str(raw["expiry_date"]),
        "expiry_at": expiry_at.isoformat(),
        "expiry_hours_at_decision": expiry_hours,
        "required_expiry_hours": required_expiry_hours,
        "expected_holding_hours": holding_hours,
        "strike_price": float(raw["strike_price"]),
        "entry_bid": bid,
        "entry_ask": ask,
        "entry_mark": raw.get("mark_price"),
        "entry_fill_basis": "OBSERVED_BEST_ASK",
        "relative_spread_pct": (ask - bid) / mid * 100.0,
        "bid_size": raw.get("bid_size"),
        "ask_size": raw.get("ask_size"),
        "bid_iv": raw.get("bid_iv"),
        "ask_iv": raw.get("ask_iv"),
        "open_interest": raw.get("open_interest"),
        "volume": raw.get("volume"),
        "greeks": dict(raw.get("greeks") or {}),
        "snapshot_first_seen_at": seen.isoformat(),
        "provider_at": raw.get("provider_at"),
        "reference_spot_price": reference_spot,
        "model_price_used": False,
        "mark_price_used_as_fill": False,
        "futures_quote_used": False,
    }
    return {"status": "OPTIONS_SHADOW_ENTRY", "reason": "FRESH_EXACT_DELTA_QUOTE", "option_entry": entry}


async def run_explicit_live_shadow_click(
    *,
    request_id: str,
    database_url: str,
    proof_config: BtcProspectiveProofRuntimeConfig,
    clock: Callable[[], datetime] | None = None,
    coindcx_http_client=None,
    delta_http_client=None,
    delta_derivatives_http_client=None,
    selection_policy: LiveShadowOptionSelectionPolicy | None = None,
) -> dict:
    """Freeze one genuine live research click using server time only."""
    request = str(request_id or "").strip()
    if not request:
        raise ValueError("request_id is required")
    database_url = str(database_url or "").strip()
    if not database_url:
        raise ValueError("database_url is required")
    proof_config = proof_config.validated()
    if not proof_config.postgres_enabled or proof_config.database_url != database_url:
        raise ValueError("live shadow click requires the enabled AlphaPilot prospective proof store")
    now = clock or (lambda: datetime.now(timezone.utc))

    click_store = PostgresBtcLiveShadowClickStore(database_url)
    delta_store = PostgresDeltaIndiaOptionsProbeStore(database_url)
    pit_store = PostgresBtcPitArchiveStore(database_url)
    proof_store = PostgresProspectiveBtcThesisTapeStore(database_url)
    await click_store.initialize()
    existing = await click_store.get(request)
    if existing is not None:
        return {"status": "LIVE_SHADOW_CLICK_ALREADY_FROZEN", "request_id": request, "record": existing}
    await delta_store.initialize()
    await pit_store.initialize()
    await proof_store.initialize()

    delta_provider = DeltaIndiaBtcOptionsPublicProvider(
        policy=DeltaIndiaOptionsProbePolicy(enabled=True, atm_strikes=7),
        client=delta_http_client,
    )
    snapshot = await asyncio.to_thread(delta_provider.capture_btc_options_snapshot)
    await delta_store.insert_first_seen(snapshot)
    frozen_snapshot = snapshot.frozen_dict()

    # Fetch reconstructible completed Delta OI before assigning decision_at. The
    # preflight cutoff is taken before the HTTP request, and rows completing after
    # that cutoff are discarded even if they appear in the response.
    delta_oi_rows = None
    oi_cutoff = _utc(now())
    delta_oi_capture = {
        "status": "UNAVAILABLE",
        "cutoff_at": oi_cutoff.isoformat(),
        "candle_count": 0,
        "latest_available_at": None,
        "error_type": None,
        "message": None,
    }
    try:
        oi_provider = DeltaIndiaBtcDerivativesPublicProvider(
            policy=DeltaIndiaBtcDerivativesContextPolicy(enabled=True, resolution="5m"),
            client=delta_derivatives_http_client,
        )
        fetched_oi = await asyncio.to_thread(
            oi_provider.fetch_oi_candles,
            start_at=oi_cutoff - timedelta(hours=2.5),
            end_at=oi_cutoff,
            resolution="5m",
        )
        delta_oi_rows = [row for row in fetched_oi if _utc(row.available_at) <= oi_cutoff]
        delta_oi_capture = {
            "status": "CAPTURED_COMPLETED_HISTORY",
            "cutoff_at": oi_cutoff.isoformat(),
            "candle_count": len(delta_oi_rows),
            "latest_available_at": None if not delta_oi_rows else _utc(delta_oi_rows[-1].available_at).isoformat(),
            "error_type": None,
            "message": None,
        }
    except Exception as exc:
        delta_oi_capture = {
            "status": "UNAVAILABLE",
            "cutoff_at": oi_cutoff.isoformat(),
            "candle_count": 0,
            "latest_available_at": None,
            "error_type": exc.__class__.__name__,
            "message": str(exc)[:300],
        }

    # Critical ordering: decision_at is assigned only after the option quote and
    # the attempted derivatives-context fetch. No caller-supplied decision time.
    decision_at = _utc(now())
    if _stamp(frozen_snapshot["first_seen_at"]) > decision_at:
        raise ValueError("captured Delta snapshot cannot be after server decision time")

    coindcx_provider = CoinDcxBtcPublicProvider(
        policy=CoinDcxBtcProviderPolicy(enabled=True),
        client=coindcx_http_client,
    )
    click_id = f"crypto-live-shadow:{request}"
    proof = await freeze_prospective_btc_thesis_from_existing_sources(
        click_id=click_id,
        decision_at=decision_at,
        provider=coindcx_provider,
        pit_store=pit_store,
        tape_policy=proof_config.tape_policy(),
        delta_oi_rows=delta_oi_rows,
    )
    frozen_thesis = proof.get("frozen_thesis") if isinstance(proof, dict) else None
    frozen_policy = proof_config.tape_policy().validated()
    selected_policy = (selection_policy or LiveShadowOptionSelectionPolicy()).validated()
    if not isinstance(frozen_thesis, dict):
        record = {
            "version": "BTC_OPTIONS_LIVE_SHADOW_CLICK_V3",
            "request_id": request,
            "click_id": click_id,
            "decision_at": decision_at.isoformat(),
            "outcome_due_at": None,
            "market_direction": "UNKNOWN",
            "shadow_status": "PROOF_INPUT_UNRESOLVED",
            "reason": str(proof.get("reason") or "BTC_PROOF_INPUT_UNRESOLVED"),
            "proof_bridge": proof,
            "delta_oi_context": delta_oi_capture,
            "option_selection_policy": selected_policy.frozen_dict(),
            "delta_snapshot_first_seen_at": frozen_snapshot["first_seen_at"],
            "option_entry": None,
            "order_placed": False,
            "live_execution": False,
            "capital_committed": 0,
        }
        await click_store.insert(record)
        return record

    await proof_store.insert_frozen(frozen_thesis)
    direction = str((frozen_thesis.get("decision") or {}).get("market_direction") or "UNKNOWN").upper()
    selected = select_delta_option_for_shadow_entry(
        frozen_snapshot,
        market_direction=direction,
        decision_at=decision_at,
        expected_holding_hours=float(frozen_policy.evaluation_horizon_hours),
        policy=selected_policy,
    )
    option_entry = selected.get("option_entry")
    shadow_status = "OPTIONS_SHADOW_ENTRY_FROZEN" if option_entry is not None else "NO_TRADE_FROZEN"
    record = {
        "version": "BTC_OPTIONS_LIVE_SHADOW_CLICK_V3",
        "request_id": request,
        "click_id": click_id,
        "decision_at": decision_at.isoformat(),
        "outcome_due_at": frozen_thesis["outcome_due_at"],
        "market_direction": direction,
        "shadow_status": shadow_status,
        "reason": selected["reason"],
        "proof_tape_fingerprint": frozen_thesis["tape_fingerprint"],
        "decision_fingerprint": frozen_thesis["decision"]["decision_fingerprint"],
        "proof_bridge": {
            "status": proof.get("status"),
            "decision_btc_price": proof.get("decision_btc_price"),
            "structure_candle_count": proof.get("structure_candle_count"),
            "pit_record_count": proof.get("pit_record_count"),
            "pit_dataset_counts": proof.get("pit_dataset_counts"),
            "delta_oi_candle_count": proof.get("delta_oi_candle_count"),
            "derivatives_price_change_pct": proof.get("derivatives_price_change_pct"),
            "pit_derivatives_evidence_status": proof.get("pit_derivatives_evidence_status"),
            "delta_oi_evidence_status": proof.get("delta_oi_evidence_status"),
            "derivatives_evidence_status": proof.get("derivatives_evidence_status"),
        },
        "delta_oi_context": delta_oi_capture,
        "frozen_underlying_policy": frozen_policy.frozen_dict(),
        "option_selection_policy": selected_policy.frozen_dict(),
        "delta_snapshot_first_seen_at": frozen_snapshot["first_seen_at"],
        "delta_reference_spot_price": frozen_snapshot["reference_spot_price"],
        "option_entry": option_entry,
        "options_exit_required_for_pnl": option_entry is not None,
        "options_exit_fill_basis": "OBSERVED_EXACT_CONTRACT_BEST_BID_ONLY" if option_entry is not None else None,
        "order_placed": False,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generated": False,
    }
    await click_store.insert(record)
    return record


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_LIVE_SHADOW_CLICK_CONTRACT_V3",
        "decision_time_source": "SERVER_CLOCK_ONLY",
        "caller_backdating_allowed": False,
        "delta_snapshot_captured_before_decision": True,
        "completed_delta_oi_attempted_before_decision": True,
        "delta_oi_completion_cutoff_precedes_http_fetch": True,
        "delta_oi_futures_context_only": True,
        "delta_candidate_venue_only": True,
        "underlying_unknown_means_no_trade": True,
        "holding_horizon_must_fit_before_exchange_expiry": True,
        "delta_btc_options_expiry": "17:30_IST",
        "fresh_bid_ask_required": True,
        "greeks_required": True,
        "oi_and_volume_required": True,
        "exact_observed_ask_required_for_entry": True,
        "exact_observed_bid_required_for_exit": True,
        "mark_price_fill_allowed": False,
        "model_price_fill_allowed": False,
        "futures_quote_substitution_allowed": False,
        "live_order_placement": False,
        "capital_committed": 0,
        "research_only": True,
    }
