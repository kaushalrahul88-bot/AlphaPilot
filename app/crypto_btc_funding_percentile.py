"""Point-in-time BTC funding percentile from immutable CoinDCX snapshots.

The calculation is descriptive market context, not a trade rule. It uses only
funding observations first-seen no later than the decision time and never lets a
later snapshot revise an earlier percentile.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Iterable

DATASET = "BTC_FUTURES_FUNDING_MARK_SNAPSHOT"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value)))


@dataclass(frozen=True)
class FundingPercentilePolicy:
    min_prior_samples: int = 20
    lookback_days: int = 30

    def validated(self) -> "FundingPercentilePolicy":
        if int(self.min_prior_samples) < 5:
            raise ValueError("min_prior_samples must be >= 5")
        if int(self.lookback_days) < 1:
            raise ValueError("lookback_days must be >= 1")
        return self


def _funding_rate(row: dict) -> float | None:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    value = payload.get("funding_rate")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def funding_percentile_from_pit_records(
    records: Iterable[dict],
    *,
    decision_at: datetime,
    policy: FundingPercentilePolicy | None = None,
) -> dict:
    policy = (policy or FundingPercentilePolicy()).validated()
    decision = _utc(decision_at)
    start = decision - timedelta(days=policy.lookback_days)
    rows: list[tuple[datetime, dict, float]] = []
    excluded_future = 0
    excluded_invalid = 0

    for row in records:
        if row.get("dataset") != DATASET or row.get("first_seen_at") is None:
            continue
        seen = _stamp(row["first_seen_at"])
        if seen > decision:
            excluded_future += 1
            continue
        if seen < start:
            continue
        rate = _funding_rate(row)
        if rate is None:
            excluded_invalid += 1
            continue
        rows.append((seen, row, rate))

    rows.sort(key=lambda item: item[0])
    if not rows:
        return {
            "status": "FUNDING_UNAVAILABLE",
            "percentile": None,
            "current_rate": None,
            "prior_sample_count": 0,
            "decision_at": decision.isoformat(),
            "excluded_future_rows": excluded_future,
            "excluded_invalid_rows": excluded_invalid,
            "may_generate_trade": False,
        }

    current_seen, current_row, current_rate = rows[-1]
    prior = [rate for seen, _row, rate in rows[:-1] if seen < current_seen]
    if len(prior) < policy.min_prior_samples:
        return {
            "status": "INSUFFICIENT_FUNDING_HISTORY",
            "percentile": None,
            "current_rate": current_rate,
            "current_first_seen_at": current_seen.isoformat(),
            "prior_sample_count": len(prior),
            "required_prior_samples": policy.min_prior_samples,
            "decision_at": decision.isoformat(),
            "excluded_future_rows": excluded_future,
            "excluded_invalid_rows": excluded_invalid,
            "may_generate_trade": False,
        }

    below = sum(1 for value in prior if value < current_rate)
    equal = sum(1 for value in prior if value == current_rate)
    percentile = (below + 0.5 * equal) / len(prior)
    return {
        "status": "FUNDING_PERCENTILE_READY",
        "percentile": round(percentile, 6),
        "current_rate": current_rate,
        "current_first_seen_at": current_seen.isoformat(),
        "current_source_key": current_row.get("source_key"),
        "prior_sample_count": len(prior),
        "lookback_days": policy.lookback_days,
        "decision_at": decision.isoformat(),
        "excluded_future_rows": excluded_future,
        "excluded_invalid_rows": excluded_invalid,
        "point_in_time_only": True,
        "may_inform_options": True,
        "may_generate_futures_trade": False,
        "may_generate_options_trade": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_FUNDING_PERCENTILE_V1",
        "raw_funding_threshold_is_trade_rule": False,
        "percentile_uses_only_prior_first_seen_history": True,
        "future_snapshots_allowed": False,
        "insufficient_history_fails_closed": True,
        "percentile_is_market_context_only": True,
        "may_inform_options": True,
        "futures_trade_generation_allowed": False,
        "options_trade_generation_allowed": False,
        "research_only": True,
    }
