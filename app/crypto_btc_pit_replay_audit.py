"""Read-only point-in-time replay audit for the live BTC research tape.

This module is deliberately diagnostic. It proves that replay inputs can be
selected strictly from information first seen at or before a replay timestamp,
and it reports data freshness/completeness separately from strategy policy.

It never creates decisions, reads prospective outcomes as model inputs, writes
to Postgres, starts collectors, generates orders, or commits capital.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

DELTA_TABLE = "crypto_btc_delta_options_probe_v1"
PIT_TABLE = "crypto_btc_pit_archive_v1"
PIT_DATASET = "BTC_FUTURES_FUNDING_MARK_SNAPSHOT"
DEFAULT_FRESHNESS_SECONDS = (120, 300, 900)
DEFAULT_CONTINUITY_GAP_SECONDS = 300
DEFAULT_CONTINUITY_GRID_SECONDS = 300

# These queries are SELECT-only by construction. Outcome/resolution tables are
# intentionally absent: a replay input may contain only information available
# at the replay timestamp.
REPLAY_SUMMARY_SQL = f"""
WITH bounds AS (
    SELECT
        GREATEST(
            (SELECT MIN(first_seen_at) FROM {DELTA_TABLE}),
            (SELECT MIN(first_seen_at) FROM {PIT_TABLE}
             WHERE dataset = '{PIT_DATASET}')
        ) AS start_at,
        LEAST(
            (SELECT MAX(first_seen_at) FROM {DELTA_TABLE}),
            (SELECT MAX(first_seen_at) FROM {PIT_TABLE}
             WHERE dataset = '{PIT_DATASET}')
        ) AS end_at
),
grid AS (
    SELECT generate_series(start_at, end_at, INTERVAL '15 minutes') AS replay_at
    FROM bounds
),
ages AS (
    SELECT
        g.replay_at,
        d.first_seen_at AS delta_at,
        p.first_seen_at AS pit_at,
        EXTRACT(EPOCH FROM (g.replay_at - d.first_seen_at)) AS delta_age_s,
        EXTRACT(EPOCH FROM (g.replay_at - p.first_seen_at)) AS pit_age_s
    FROM grid g
    LEFT JOIN LATERAL (
        SELECT first_seen_at
        FROM {DELTA_TABLE}
        WHERE first_seen_at <= g.replay_at
        ORDER BY first_seen_at DESC, snapshot_id DESC
        LIMIT 1
    ) d ON TRUE
    LEFT JOIN LATERAL (
        SELECT first_seen_at
        FROM {PIT_TABLE}
        WHERE dataset = '{PIT_DATASET}'
          AND first_seen_at <= g.replay_at
        ORDER BY first_seen_at DESC, natural_key DESC
        LIMIT 1
    ) p ON TRUE
)
SELECT
    COUNT(*)::BIGINT AS points,
    COUNT(*) FILTER (WHERE delta_at > replay_at OR pit_at > replay_at)::BIGINT AS lookahead,
    COUNT(*) FILTER (WHERE delta_age_s <= 120 AND pit_age_s <= 120)::BIGINT AS fresh2m,
    COUNT(*) FILTER (WHERE delta_age_s <= 300 AND pit_age_s <= 300)::BIGINT AS fresh5m,
    COUNT(*) FILTER (WHERE delta_age_s <= 900 AND pit_age_s <= 900)::BIGINT AS fresh15m,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delta_age_s) AS med_delta_s,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pit_age_s) AS med_pit_s,
    MAX(delta_age_s) AS max_delta_s,
    MAX(pit_age_s) AS max_pit_s
FROM ages;
"""

QUOTE_COMPLETENESS_SQL = f"""
WITH bounds AS (
    SELECT
        GREATEST(
            (SELECT MIN(first_seen_at) FROM {DELTA_TABLE}),
            (SELECT MIN(first_seen_at) FROM {PIT_TABLE}
             WHERE dataset = '{PIT_DATASET}')
        ) AS start_at,
        LEAST(
            (SELECT MAX(first_seen_at) FROM {DELTA_TABLE}),
            (SELECT MAX(first_seen_at) FROM {PIT_TABLE}
             WHERE dataset = '{PIT_DATASET}')
        ) AS end_at
),
grid AS (
    SELECT generate_series(start_at, end_at, INTERVAL '15 minutes') AS replay_at
    FROM bounds
),
chosen AS (
    SELECT g.replay_at, d.payload
    FROM grid g
    LEFT JOIN LATERAL (
        SELECT payload
        FROM {DELTA_TABLE}
        WHERE first_seen_at <= g.replay_at
        ORDER BY first_seen_at DESC, snapshot_id DESC
        LIMIT 1
    ) d ON TRUE
),
quotes AS (
    SELECT c.replay_at, q.quote
    FROM chosen c
    CROSS JOIN LATERAL jsonb_array_elements(
        COALESCE(c.payload->'quotes', '[]'::jsonb)
    ) q(quote)
)
SELECT
    COUNT(*)::BIGINT AS quote_rows,
    COUNT(*) FILTER (
        WHERE quote->>'best_bid' IS NOT NULL
          AND quote->>'best_ask' IS NOT NULL
          AND quote->>'mark_price' IS NOT NULL
          AND jsonb_typeof(quote->'greeks') = 'object'
          AND quote->>'open_interest' IS NOT NULL
          AND quote->>'volume' IS NOT NULL
    )::BIGINT AS complete_rows
FROM quotes;
"""


def _positive_int(value: int, name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def build_continuous_replay_summary_sql(
    *,
    max_gap_seconds: int = DEFAULT_CONTINUITY_GAP_SECONDS,
    grid_seconds: int = DEFAULT_CONTINUITY_GRID_SECONDS,
) -> str:
    """Build a SELECT-only audit for the newest shared continuous data segment."""
    gap = _positive_int(max_gap_seconds, "max_gap_seconds")
    grid = _positive_int(grid_seconds, "grid_seconds")
    return f"""
WITH delta_ordered AS (
    SELECT
        first_seen_at,
        LAG(first_seen_at) OVER (
            ORDER BY first_seen_at, snapshot_id
        ) AS prev_at
    FROM {DELTA_TABLE}
),
delta_segment AS (
    SELECT
        COALESCE(
            MAX(first_seen_at) FILTER (
                WHERE prev_at IS NOT NULL
                  AND EXTRACT(EPOCH FROM (first_seen_at - prev_at)) > {gap}
            ),
            MIN(first_seen_at)
        ) AS start_at,
        MAX(first_seen_at) AS end_at
    FROM delta_ordered
),
pit_ordered AS (
    SELECT
        first_seen_at,
        LAG(first_seen_at) OVER (
            ORDER BY first_seen_at, natural_key
        ) AS prev_at
    FROM {PIT_TABLE}
    WHERE dataset = '{PIT_DATASET}'
),
pit_segment AS (
    SELECT
        COALESCE(
            MAX(first_seen_at) FILTER (
                WHERE prev_at IS NOT NULL
                  AND EXTRACT(EPOCH FROM (first_seen_at - prev_at)) > {gap}
            ),
            MIN(first_seen_at)
        ) AS start_at,
        MAX(first_seen_at) AS end_at
    FROM pit_ordered
),
bounds AS (
    SELECT
        GREATEST(d.start_at, p.start_at) AS start_at,
        LEAST(d.end_at, p.end_at) AS end_at
    FROM delta_segment d
    CROSS JOIN pit_segment p
),
grid AS (
    SELECT generate_series(
        start_at,
        end_at,
        INTERVAL '{grid} seconds'
    ) AS replay_at
    FROM bounds
    WHERE start_at IS NOT NULL
      AND end_at IS NOT NULL
      AND start_at <= end_at
),
ages AS (
    SELECT
        g.replay_at,
        d.first_seen_at AS delta_at,
        p.first_seen_at AS pit_at,
        EXTRACT(EPOCH FROM (g.replay_at - d.first_seen_at)) AS delta_age_s,
        EXTRACT(EPOCH FROM (g.replay_at - p.first_seen_at)) AS pit_age_s
    FROM grid g
    LEFT JOIN LATERAL (
        SELECT first_seen_at
        FROM {DELTA_TABLE}
        WHERE first_seen_at <= g.replay_at
        ORDER BY first_seen_at DESC, snapshot_id DESC
        LIMIT 1
    ) d ON TRUE
    LEFT JOIN LATERAL (
        SELECT first_seen_at
        FROM {PIT_TABLE}
        WHERE dataset = '{PIT_DATASET}'
          AND first_seen_at <= g.replay_at
        ORDER BY first_seen_at DESC, natural_key DESC
        LIMIT 1
    ) p ON TRUE
)
SELECT
    (SELECT start_at FROM bounds) AS start_at,
    (SELECT end_at FROM bounds) AS end_at,
    {gap}::BIGINT AS max_gap_seconds,
    {grid}::BIGINT AS grid_seconds,
    COUNT(*)::BIGINT AS points,
    COUNT(*) FILTER (
        WHERE delta_at > replay_at OR pit_at > replay_at
    )::BIGINT AS lookahead,
    COUNT(*) FILTER (
        WHERE delta_age_s <= 120 AND pit_age_s <= 120
    )::BIGINT AS fresh2m,
    COUNT(*) FILTER (
        WHERE delta_age_s <= 300 AND pit_age_s <= 300
    )::BIGINT AS fresh5m,
    COUNT(*) FILTER (
        WHERE delta_age_s <= 900 AND pit_age_s <= 900
    )::BIGINT AS fresh15m,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delta_age_s) AS med_delta_s,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pit_age_s) AS med_pit_s,
    MAX(delta_age_s) AS max_delta_s,
    MAX(pit_age_s) AS max_pit_s
FROM ages;
"""


CONTINUOUS_REPLAY_SUMMARY_SQL = build_continuous_replay_summary_sql()


@dataclass(frozen=True)
class ReplaySelection:
    replay_at: datetime
    selected_at: datetime
    source_id: str
    row: Mapping[str, Any]

    @property
    def age_seconds(self) -> float:
        return (self.replay_at - self.selected_at).total_seconds()


@dataclass(frozen=True)
class ContinuityWindow:
    start_at: datetime
    end_at: datetime
    max_gap_seconds: int

    @property
    def span_seconds(self) -> float:
        return (self.end_at - self.start_at).total_seconds()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("point-in-time replay timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def latest_contiguous_segment(
    rows: Iterable[Mapping[str, Any]],
    *,
    timestamp_key: str = "first_seen_at",
    max_gap_seconds: int = DEFAULT_CONTINUITY_GAP_SECONDS,
) -> tuple[datetime, datetime] | None:
    """Return the newest segment after the last gap larger than the threshold."""
    threshold = _positive_int(max_gap_seconds, "max_gap_seconds")
    observed_times: list[datetime] = []
    for row in rows:
        observed = row.get(timestamp_key)
        if not isinstance(observed, datetime):
            continue
        observed_times.append(_aware_utc(observed))

    if not observed_times:
        return None

    times = sorted(observed_times)
    start_at = times[0]
    previous = times[0]
    for observed in times[1:]:
        if (observed - previous).total_seconds() > threshold:
            start_at = observed
        previous = observed
    return start_at, times[-1]


def latest_shared_continuity_window(
    delta_rows: Iterable[Mapping[str, Any]],
    pit_rows: Iterable[Mapping[str, Any]],
    *,
    max_gap_seconds: int = DEFAULT_CONTINUITY_GAP_SECONDS,
) -> ContinuityWindow | None:
    """Intersect the newest continuous Delta and PIT segments."""
    threshold = _positive_int(max_gap_seconds, "max_gap_seconds")
    delta = latest_contiguous_segment(
        delta_rows,
        max_gap_seconds=threshold,
    )
    pit = latest_contiguous_segment(
        pit_rows,
        max_gap_seconds=threshold,
    )
    if delta is None or pit is None:
        return None
    start_at = max(delta[0], pit[0])
    end_at = min(delta[1], pit[1])
    if start_at > end_at:
        return None
    return ContinuityWindow(
        start_at=start_at,
        end_at=end_at,
        max_gap_seconds=threshold,
    )


def select_latest_at_or_before(
    rows: Iterable[Mapping[str, Any]],
    replay_at: datetime,
    *,
    timestamp_key: str = "first_seen_at",
    id_key: str,
) -> ReplaySelection | None:
    """Select the newest deterministic row whose timestamp is not after replay_at."""
    replay_utc = _aware_utc(replay_at)
    best: tuple[datetime, str, Mapping[str, Any]] | None = None
    for row in rows:
        observed = row.get(timestamp_key)
        if not isinstance(observed, datetime):
            continue
        observed_utc = _aware_utc(observed)
        if observed_utc > replay_utc:
            continue
        source_id = str(row.get(id_key, ""))
        candidate = (observed_utc, source_id, row)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return None
    selected_at, source_id, row = best
    return ReplaySelection(
        replay_at=replay_utc,
        selected_at=selected_at,
        source_id=source_id,
        row=row,
    )


def is_fresh(selection: ReplaySelection | None, max_age_seconds: int) -> bool:
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    return selection is not None and 0 <= selection.age_seconds <= max_age_seconds


def quote_is_complete(quote: Mapping[str, Any]) -> bool:
    required = ("best_bid", "best_ask", "mark_price", "open_interest", "volume")
    return all(quote.get(key) is not None for key in required) and isinstance(
        quote.get("greeks"), Mapping
    )


def audit_replay_points(
    replay_times: Sequence[datetime],
    delta_rows: Iterable[Mapping[str, Any]],
    pit_rows: Iterable[Mapping[str, Any]],
    *,
    freshness_seconds: Sequence[int] = DEFAULT_FRESHNESS_SECONDS,
) -> dict[str, Any]:
    """Audit no-lookahead selection and freshness without making strategy decisions."""
    delta = tuple(delta_rows)
    pit = tuple(pit_rows)
    cutoffs = tuple(int(value) for value in freshness_seconds)
    if any(value < 0 for value in cutoffs):
        raise ValueError("freshness cutoffs must be non-negative")

    points: list[dict[str, Any]] = []
    lookahead_violations = 0
    fresh_counts = {seconds: 0 for seconds in cutoffs}

    for raw_replay_at in replay_times:
        replay_at = _aware_utc(raw_replay_at)
        delta_selection = select_latest_at_or_before(
            delta, replay_at, id_key="snapshot_id"
        )
        pit_selection = select_latest_at_or_before(
            pit, replay_at, id_key="natural_key"
        )
        selections = (delta_selection, pit_selection)
        if any(
            selected is not None and selected.selected_at > replay_at
            for selected in selections
        ):
            lookahead_violations += 1

        for seconds in cutoffs:
            if all(is_fresh(selected, seconds) for selected in selections):
                fresh_counts[seconds] += 1

        points.append(
            {
                "replay_at": replay_at,
                "delta": delta_selection,
                "pit": pit_selection,
                "both_available": all(selected is not None for selected in selections),
            }
        )

    return {
        "point_count": len(points),
        "lookahead_violations": lookahead_violations,
        "fresh_counts": fresh_counts,
        "points": points,
        "diagnostic_only": True,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_PIT_REPLAY_AUDIT_V2",
        "read_only": True,
        "database_writes": False,
        "decisions_created": False,
        "prospective_outcomes_used_as_input": False,
        "prospective_resolutions_used_as_input": False,
        "diagnostic_freshness_cutoffs_are_strategy_policy": False,
        "continuity_gap_threshold_is_strategy_policy": False,
        "continuity_window_is_diagnostic_only": True,
        "live_execution": False,
        "capital_committed": 0,
        "options_and_futures_trade_generation_separate": True,
    }
