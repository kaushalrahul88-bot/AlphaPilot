from __future__ import annotations

import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .copper_commodity_brain_shadow_v1 import evaluate_copper_commodity_brain_shadow
from .copper_option_participation_v1 import build_option_participation_snapshot
from .copper_research_brain import _build_copper_snapshot_clean, clean_ohlcv

IST = ZoneInfo("Asia/Kolkata")
MODE = "COPPER_COMMODITY_BRAIN_PIT_REPLAY_V1"
RULE_FREEZE_COMMIT = "9f6c2dc734484ddc7909ba75b49e76d329fa379c"
CANDLE_MINUTES = 5
GENERIC_OPTION_PROVENANCE = "COMMODITY_OPTION_SNAPSHOTS_LIVE_CAPTURED_GENERIC_PIT_TAPE"


def _stamp(value) -> datetime:
    return parse_ist_timestamp(value).astimezone(IST)


def _completed_candles(candles, click: datetime) -> list[list]:
    """Return only historical bars that would have completed by the replay click."""
    visible: list[list] = []
    for row in clean_ohlcv(candles or []):
        try:
            start = _stamp(row[0])
        except (TypeError, ValueError, OverflowError):
            continue
        if start + timedelta(minutes=CANDLE_MINUTES) <= click:
            visible.append(row)
    return visible


def _visible_generic_option_rows(rows: list[dict], click: datetime) -> list[dict]:
    """Fail closed on every timestamp carried by the mutable generic PIT row.

    `commodity_option_snapshots` upserts within the same contract/bucket. Therefore
    the final stored row is visible only after its final stored `collected_at`; the
    replay never assumes an overwritten earlier value.
    """
    visible: list[dict] = []
    for row in rows or []:
        try:
            sample = _stamp(row.get("sample_bucket_at"))
            observed = _stamp(row.get("observed_at"))
            collected = _stamp(row.get("collected_at"))
        except Exception:
            continue
        if sample <= click and observed <= click and collected <= click:
            visible.append(dict(row))
    return visible


def _generic_pit_participation(rows: list[dict], click: datetime) -> dict:
    visible = _visible_generic_option_rows(rows, click)
    snapshot = build_option_participation_snapshot(visible, as_of=click)
    snapshot = copy.deepcopy(snapshot)

    reason_map = {
        "NO_VISIBLE_FIRST_SEEN_OPTION_BUCKET": "NO_VISIBLE_GENERIC_PIT_OPTION_BUCKET",
        "NO_PREVIOUS_IMMUTABLE_OPTION_BUCKET": "NO_PREVIOUS_VISIBLE_GENERIC_PIT_OPTION_BUCKET",
    }
    if snapshot.get("reason") in reason_map:
        snapshot["reason"] = reason_map[snapshot["reason"]]

    snapshot.update(
        {
            "first_seen_immutable": False,
            "provenance_id": GENERIC_OPTION_PROVENANCE,
            "source_table": "commodity_option_snapshots",
            "source_class": "LIVE_CAPTURED_GENERIC_PIT_TAPE",
            "source_mutability": "UPSERT_WITHIN_PROVIDER_CONTRACT_BUCKET",
            "visibility_rule": "sample_bucket_at<=click AND observed_at<=click AND final_stored_collected_at<=click",
            "historical_backfill_used": False,
            "mutable_generic_fallback_used": True,
            "replay_class": "PIT_REPLAY",
            "prospective": False,
            "eligible_for_prospective_memory": False,
            "visible_rows": len(visible),
        }
    )
    return snapshot


def _default_groups(snapshot: dict, participation: dict) -> dict:
    latest_bucket = participation.get("latest_bucket_at")
    return {
        "primary_market": {
            "MCX_COPPER": {
                "status": "AVAILABLE",
                "perception_status": "AVAILABLE",
                "perception_reason": None,
                "perception_snapshot": snapshot,
                "source_class": "HISTORICAL_COMPLETED_BAR_RECONSTRUCTION",
                "prospective": False,
            }
        },
        "option_market": {
            "MCX_COPPER_OPTION": {
                "status": "AVAILABLE" if latest_bucket else "UNAVAILABLE",
                "sample_bucket_at": latest_bucket,
                "first_seen_immutable": False,
                "participation_snapshot": participation,
                "registered_participation_rule_version": participation.get("rule_version"),
                "registered_change_directional_vote_allowed": True,
                "raw_oi_directional_vote_allowed": False,
                "source_table": "commodity_option_snapshots",
                "source_class": "LIVE_CAPTURED_GENERIC_PIT_TAPE",
            }
        },
        "global_copper": {
            "COMEX_HG": {"status": "UNAVAILABLE", "reason": "NO_TIMESTAMP_SAFE_REPLAY_FEED_SUPPLIED"},
            "LME_COPPER": {"status": "UNAVAILABLE", "reason": "NO_TIMESTAMP_SAFE_REPLAY_FEED_SUPPLIED"},
        },
        "china_macro": {
            "MACRO_RELEASE": {"status": "UNAVAILABLE", "reason": "NO_PIT_REPLAY_RECORDS_SUPPLIED"},
        },
        "news": {
            "COPPER_NEWS": {"status": "UNAVAILABLE", "reason": "NO_FIRST_DETECTED_REPLAY_NEWS_SUPPLIED"},
        },
        "experience_memory": {
            "DIRECTION_MEMORY": {"status": "UNAVAILABLE", "reason": "REPLAY_NOT_ELIGIBLE_FOR_PROSPECTIVE_MEMORY"},
        },
        "currency": {},
        "positioning": {},
    }


def _merge_groups(base: dict, extra: dict | None) -> dict:
    merged = copy.deepcopy(base)
    for group_name, group_value in (extra or {}).items():
        if isinstance(group_value, dict) and isinstance(merged.get(group_name), dict):
            merged[group_name].update(copy.deepcopy(group_value))
        else:
            merged[group_name] = copy.deepcopy(group_value)
    return merged


def _underlying_crosscheck(option_rows: list[dict], completed: list[list], click: datetime) -> dict:
    visible = _visible_generic_option_rows(option_rows, click)
    option_prices = []
    if visible:
        latest_bucket = max(_stamp(row["sample_bucket_at"]) for row in visible)
        for row in visible:
            if _stamp(row["sample_bucket_at"]) == latest_bucket:
                try:
                    value = float(row.get("underlying_price"))
                except (TypeError, ValueError, OverflowError):
                    continue
                if value > 0:
                    option_prices.append(value)
    option_price = option_prices[0] if option_prices else None
    candle_price = float(completed[-1][4]) if completed else None
    gap_pct = None
    if option_price and candle_price:
        gap_pct = (option_price / candle_price - 1.0) * 100.0
    return {
        "role": "PROVENANCE_CROSSCHECK_ONLY",
        "affects_direction": False,
        "latest_option_underlying_price": option_price,
        "latest_completed_candle_close": candle_price,
        "gap_pct": gap_pct,
        "option_underlying_prices_in_latest_bucket": len(set(option_prices)),
    }


def evaluate_copper_pit_replay(
    *,
    candles,
    option_rows: list[dict],
    click_at,
    context_groups: dict | None = None,
) -> dict:
    """Evaluate frozen Copper Shared Brain V1 on historical live-captured PIT inputs.

    This function is intentionally outcome-blind and has no persistence side effects.
    Historical/retrieved candles are reconstructable data and are admitted only after
    their bar close. Generic option snapshots retain their actual live collection
    timestamps and are admitted only when all stored timestamps are visible.
    """
    click = _stamp(click_at)
    completed = _completed_candles(candles, click)
    if len(completed) < 51:
        return {
            "mode": MODE,
            "evaluation_class": "PIT_REPLAY",
            "status": "INSUFFICIENT_COMPLETED_CANDLE_HISTORY",
            "click_at": click.isoformat(),
            "completed_candles": len(completed),
            "minimum_completed_candles": 51,
            "prospective": False,
            "eligible_for_prospective_memory": False,
            "outcome_blind_at_evaluation": True,
            "brain_rule_freeze_commit": RULE_FREEZE_COMMIT,
            "historical_records_changed": False,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "capital_committed": 0,
        }

    snapshot = _build_copper_snapshot_clean(completed, len(completed) - 1)
    participation = _generic_pit_participation(option_rows, click)
    board = {
        "as_of": click.isoformat(),
        "groups": _merge_groups(_default_groups(snapshot, participation), context_groups),
        "rules": [
            "PIT_REPLAY only; this evaluation is not prospective evidence.",
            "Historical underlying candles are reconstructable and visible only after bar completion.",
            "Generic live option rows are visible only after sample, observed and final stored collection timestamps.",
            "Replay output cannot enter prospective memory or rewrite stored predictions.",
        ],
    }
    brain = evaluate_copper_commodity_brain_shadow(board)

    return {
        "mode": MODE,
        "evaluation_class": "PIT_REPLAY",
        "status": "EVALUATED",
        "click_at": click.isoformat(),
        "prospective": False,
        "eligible_for_prospective_memory": False,
        "outcome_blind_at_evaluation": True,
        "brain_rule_freeze_commit": RULE_FREEZE_COMMIT,
        "historical_records_changed": False,
        "underlying_candle_provenance": {
            "source_class": "HISTORICAL_COMPLETED_BAR_RECONSTRUCTION",
            "historical_live_collection_claimed": False,
            "completed_bar_rule": "candle_start + 5m <= click",
            "visible_completed_candles": len(completed),
            "latest_visible_candle_at": str(completed[-1][0]),
        },
        "option_provenance": {
            "source_table": "commodity_option_snapshots",
            "source_class": "LIVE_CAPTURED_GENERIC_PIT_TAPE",
            "first_seen_immutable": False,
            "upsert_semantics_acknowledged": True,
            "visibility_rule": participation["visibility_rule"],
            "visible_rows": participation.get("visible_rows", 0),
        },
        "underlying_crosscheck": _underlying_crosscheck(option_rows, completed, click),
        "brain": brain,
        "execution": {
            "paper_signal_only": True,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "capital_committed": 0,
        },
    }


def replay_contract() -> dict:
    return {
        "version": MODE,
        "brain_rule_freeze_commit": RULE_FREEZE_COMMIT,
        "evaluation_class": "PIT_REPLAY",
        "prospective": False,
        "eligible_for_prospective_memory": False,
        "outcomes_used_at_evaluation": False,
        "historical_underlying_reconstruction_allowed": True,
        "historical_underlying_requires_completed_bar": True,
        "generic_live_option_tape_allowed": True,
        "generic_live_option_tape_immutable": False,
        "generic_option_final_collected_at_visibility_required": True,
        "replay_rule_tuning_allowed": False,
        "persistence_side_effects": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
    }
