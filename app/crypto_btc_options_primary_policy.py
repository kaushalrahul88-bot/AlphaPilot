"""Options-primary policy boundary for AlphaPilot Crypto Brain.

AlphaPilot may observe Spot/Futures/derivatives context while researching a BTC
Options setup, but only genuine Options contract/quote data may satisfy Options
selection, entry/exit economics, or realized shadow P&L requirements.

This module intentionally does not discover or reverse-engineer an undocumented
CoinDCX Options endpoint. Until a documented or explicitly authorized Options
feed is configured, Options market-data capture remains unavailable and the
trade path must fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

PRIMARY_TRADE_INSTRUMENT = "OPTIONS"
FUTURES_CONTEXT_ROLE = "OPTIONS_CONTEXT_ONLY"
COINDCX_OPTIONS_PIT_DATASET = "COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES"
COINDCX_OPTIONS_EXIT_DATASET = "BTC_OPTION_EXIT_QUOTES"
ALLOWED_OPTIONS_ECONOMIC_DATASETS = frozenset({
    COINDCX_OPTIONS_PIT_DATASET,
    COINDCX_OPTIONS_EXIT_DATASET,
})


def tag_futures_context_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy explicitly preventing Futures data from posing as Options data."""
    return {
        **dict(payload),
        "primary_trade_instrument": PRIMARY_TRADE_INSTRUMENT,
        "instrument_role": FUTURES_CONTEXT_ROLE,
        "context_only": True,
        "may_satisfy_options_contract_quote": False,
        "may_select_options_contract": False,
        "may_measure_options_entry_or_exit": False,
        "may_generate_options_trade_by_itself": False,
        "may_generate_futures_trade": False,
        "futures_quote_substitution_allowed": False,
    }


@dataclass(frozen=True)
class CoinDcxOptionsCaptureReadiness:
    documented_or_authorized_options_feed: bool = False
    endpoint_or_feed_id: str = ""

    def validated(self) -> "CoinDcxOptionsCaptureReadiness":
        feed_id = str(self.endpoint_or_feed_id or "").strip()
        if self.documented_or_authorized_options_feed and not feed_id:
            raise ValueError("verified CoinDCX Options feed requires endpoint_or_feed_id")
        return self

    def status(self) -> dict[str, Any]:
        self.validated()
        ready = bool(self.documented_or_authorized_options_feed)
        return {
            "version": "COINDCX_BTC_OPTIONS_CAPTURE_READINESS_V1",
            "primary_trade_instrument": PRIMARY_TRADE_INSTRUMENT,
            "venue": "COINDCX",
            "options_pit_capture_status": (
                "READY_FOR_IMPLEMENTATION" if ready
                else "BLOCKED_NO_VERIFIED_DOCUMENTED_OR_AUTHORIZED_OPTIONS_FEED"
            ),
            "documented_or_authorized_options_feed": ready,
            "endpoint_or_feed_id_configured": bool(str(self.endpoint_or_feed_id or "").strip()),
            "undocumented_endpoint_reverse_engineering_allowed": False,
            "futures_context_allowed": True,
            "futures_context_role": FUTURES_CONTEXT_ROLE,
            "futures_may_satisfy_options_contract_quote": False,
            "options_quote_required_for_economic_replay": True,
            "options_trade_generation_enabled": False,
            "futures_trade_generation_enabled": False,
            "live_execution_enabled": False,
            "capital_committed": 0,
        }


def options_economic_data_gate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fail closed unless genuine Options contract/quote records are present.

    This is deliberately a data-admission gate, not a trade generator. It does not
    infer a contract from BTC Spot/Futures prices and does not value an option from
    a model in place of an observed CoinDCX/verified archive quote.
    """
    records = list(rows)
    qualifying = [
        row for row in records
        if str(row.get("dataset") or "") in ALLOWED_OPTIONS_ECONOMIC_DATASETS
    ]
    if not qualifying:
        return {
            "status": "OPTIONS_ECONOMIC_DATA_UNAVAILABLE",
            "primary_trade_instrument": PRIMARY_TRADE_INSTRUMENT,
            "qualifying_options_records": 0,
            "futures_substitution_used": False,
            "model_price_substitution_used": False,
            "trade_generated": False,
        }
    return {
        "status": "OPTIONS_ECONOMIC_DATA_PRESENT",
        "primary_trade_instrument": PRIMARY_TRADE_INSTRUMENT,
        "qualifying_options_records": len(qualifying),
        "futures_substitution_used": False,
        "model_price_substitution_used": False,
        "trade_generated": False,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "CRYPTO_OPTIONS_PRIMARY_BOUNDARY_V1",
        "primary_trade_instrument": PRIMARY_TRADE_INSTRUMENT,
        "futures_context_allowed": True,
        "futures_context_only": True,
        "futures_can_substitute_for_options_contract_or_quote": False,
        "spot_can_substitute_for_options_contract_or_quote": False,
        "model_price_can_substitute_for_observed_options_quote": False,
        "undocumented_options_endpoint_reverse_engineering_allowed": False,
        "options_economic_replay_requires_genuine_options_records": True,
        "options_trade_generation_enabled": False,
        "futures_trade_generation_enabled": False,
        "live_execution_enabled": False,
        "research_only": True,
    }
