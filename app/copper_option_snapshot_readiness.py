from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo



IST = ZoneInfo("Asia/Kolkata")
PROVIDER = "GROWW"
EXPECTED_SCHEDULED_BUCKETS_PER_DAY = 173
DESCRIPTIVE_REPLAY_MIN_TRADING_DAYS = 20


def _pct(numerator, denominator):
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator) * 100.0, 2)


def summarize_snapshot_readiness(rows, expected_buckets_per_day=EXPECTED_SCHEDULED_BUCKETS_PER_DAY):
    daily = []
    total_snapshots = 0
    total_ce = 0
    total_pe = 0
    total_underlying = 0
    total_two_sided = 0
    total_buckets = 0

    for row in rows or []:
        (
            day,
            buckets,
            snapshots,
            ce,
            pe,
            underlying_count,
            two_sided,
            first_at,
            last_at,
            contracts,
        ) = row
        buckets = int(buckets or 0)
        snapshots = int(snapshots or 0)
        ce = int(ce or 0)
        pe = int(pe or 0)
        underlying_count = int(underlying_count or 0)
        two_sided = int(two_sided or 0)
        contracts = int(contracts or 0)
        total_snapshots += snapshots
        total_ce += ce
        total_pe += pe
        total_underlying += underlying_count
        total_two_sided += two_sided
        total_buckets += buckets
        daily.append({
            "day": day.isoformat() if hasattr(day, "isoformat") else str(day),
            "distinct_5m_buckets": buckets,
            "scheduled_bucket_coverage_pct": _pct(
                min(buckets, expected_buckets_per_day),
                expected_buckets_per_day,
            ),
            "snapshots": snapshots,
            "ce_snapshots": ce,
            "pe_snapshots": pe,
            "underlying_price_coverage_pct": _pct(underlying_count, snapshots),
            "two_sided_quote_coverage_pct": _pct(two_sided, snapshots),
            "contracts": contracts,
            "first_bucket_at": first_at.isoformat() if first_at else None,
            "last_bucket_at": last_at.isoformat() if last_at else None,
            "both_option_sides_present": ce > 0 and pe > 0,
        })

    trading_days = len(daily)
    if trading_days == 0:
        sample_status = "NO_DATA"
    elif trading_days < 5:
        sample_status = "ACCUMULATING"
    elif trading_days < DESCRIPTIVE_REPLAY_MIN_TRADING_DAYS:
        sample_status = "EARLY_RESEARCH_SAMPLE"
    else:
        sample_status = "DESCRIPTIVE_REPLAY_SAMPLE_AVAILABLE"

    bucket_counts = [item["distinct_5m_buckets"] for item in daily]
    buckets_per_day_median = float(median(bucket_counts)) if bucket_counts else 0.0
    days_both_sides = sum(1 for item in daily if item["both_option_sides_present"])

    return {
        "mode": "COPPER_OPTION_SNAPSHOT_READINESS_V1",
        "status": sample_status,
        "research_only": True,
        "descriptive_only": True,
        "strategy_gate": False,
        "promotion_eligible": False,
        "data_type": "LIVE_5M_LTP_SNAPSHOTS_NOT_OHLC",
        "expected_scheduled_buckets_per_day": int(expected_buckets_per_day),
        "minimum_trading_days_for_descriptive_replay_review": DESCRIPTIVE_REPLAY_MIN_TRADING_DAYS,
        "trading_days": trading_days,
        "snapshots": total_snapshots,
        "distinct_5m_buckets": total_buckets,
        "ce_snapshots": total_ce,
        "pe_snapshots": total_pe,
        "days_with_both_option_sides": days_both_sides,
        "both_sides_day_coverage_pct": _pct(days_both_sides, trading_days),
        "underlying_price_coverage_pct": _pct(total_underlying, total_snapshots),
        "two_sided_quote_coverage_pct": _pct(total_two_sided, total_snapshots),
        "median_distinct_buckets_per_day": round(buckets_per_day_median, 2),
        "median_scheduled_bucket_coverage_pct": _pct(
            min(buckets_per_day_median, expected_buckets_per_day),
            expected_buckets_per_day,
        ),
        "daily": daily,
        "interpretation": (
            "Readiness describes forward data quantity and observable quote quality only. "
            "It cannot select a setup, validate an economic edge, or promote Market Brain."
        ),
    }


def _load_sync(database_url, symbol, days):
    import psycopg

    cutoff = datetime.now(IST) - timedelta(days=max(1, min(int(days), 3650)))
    with psycopg.connect(str(database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.commodity_option_snapshots')")
            relation = cursor.fetchone()
            if not relation or relation[0] is None:
                return summarize_snapshot_readiness([])

            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='commodity_option_snapshots'
                """
            )
            columns = {row[0] for row in cursor.fetchall()}

            underlying_expr = (
                "COUNT(*) FILTER (WHERE underlying_price IS NOT NULL AND underlying_price > 0)"
                if "underlying_price" in columns
                else "0"
            )
            two_sided_expr = (
                "COUNT(*) FILTER (WHERE bid_price IS NOT NULL AND bid_price > 0 "
                "AND ask_price IS NOT NULL AND ask_price >= bid_price)"
                if {"bid_price", "ask_price"}.issubset(columns)
                else "0"
            )
            sql = f"""
                SELECT
                    (sample_bucket_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
                    COUNT(DISTINCT sample_bucket_at) AS buckets,
                    COUNT(*) AS snapshots,
                    COUNT(*) FILTER (WHERE option_type = 'CE') AS ce,
                    COUNT(*) FILTER (WHERE option_type = 'PE') AS pe,
                    {underlying_expr} AS underlying_count,
                    {two_sided_expr} AS two_sided,
                    MIN(sample_bucket_at) AS first_at,
                    MAX(sample_bucket_at) AS last_at,
                    COUNT(DISTINCT trading_symbol) AS contracts
                FROM commodity_option_snapshots
                WHERE provider = %s
                  AND underlying_symbol = %s
                  AND sample_bucket_at >= %s
                GROUP BY 1
                ORDER BY 1 ASC
            """
            cursor.execute(sql, (PROVIDER, str(symbol).upper(), cutoff))
            rows = cursor.fetchall()
    return summarize_snapshot_readiness(rows)


async def run_snapshot_readiness(database_url, symbol="COPPER", days=60):
    if not str(database_url or "").strip():
        raise ValueError("DATABASE_URL is required for option snapshot readiness")
    return await asyncio.to_thread(_load_sync, database_url, symbol, days)
