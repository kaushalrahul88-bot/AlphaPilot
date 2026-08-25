from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta

from .backtest import _historical, run_backtest
from .market_brain_context_research import SECTOR, STOCKS, SYMBOLS
from .market_brain_setup_expectancy import (
    CONTEXT_WINDOW_END,
    CONTEXT_WINDOW_START,
    SETUP_SYMBOLS,
    _inside_context_window,
    _minute_key,
    _summary,
)

FEATURE_NAMES = (
    "breadth_alignment",
    "flow_alignment",
    "nifty_vwap_alignment",
    "bank_vwap_alignment",
    "nifty_trend_alignment",
    "bank_trend_alignment",
    "volatility_expansion",
)
DEVELOPMENT_START = "2026-05-25"
DEVELOPMENT_END = "2026-08-10"
HOLDOUT_START = "2026-08-11"
HOLDOUT_END = "2026-08-21"
MODEL_ITERATIONS = 1200
MODEL_LEARNING_RATE = 0.05
MODEL_L2 = 0.20
MIN_HOLDOUT_OBS = 36
MIN_HOLDOUT_CLASS = 10
MIN_BAND_OBS = 12

FROZEN_BLOCKS = {
    ("2026-05-25", "2026-06-05"): ("S-0A", "DEVELOPMENT"),
    ("2026-06-08", "2026-06-19"): ("S-0B", "DEVELOPMENT"),
    ("2026-06-22", "2026-07-03"): ("S-0C", "DEVELOPMENT"),
    ("2026-07-06", "2026-07-17"): ("S-1", "DEVELOPMENT"),
    ("2026-07-20", "2026-07-31"): ("S-2", "DEVELOPMENT"),
    ("2026-08-03", "2026-08-10"): ("S-3", "DEVELOPMENT"),
    (HOLDOUT_START, HOLDOUT_END): ("H-1", "HOLDOUT"),
}


def _n(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _clip(value, low, high):
    return max(low, min(high, value))


def _day(value):
    key = _minute_key(value)
    return key[:10] if key else ""


def _slot(value):
    key = _minute_key(value)
    return key[-5:] if key else ""


def _continuous_symbol_at(symbol, rows, index):
    if index < 1 or index >= len(rows):
        return None
    last = rows[index]
    last_day = _day(last[0])
    if not last_day:
        return None
    window = rows[: index + 1]
    session = [row for row in window if _day(row[0]) == last_day]
    prior = [row for row in window if _day(row[0]) < last_day]
    if not session or not prior:
        return None

    close = _n(last[4])
    prior_close = _n(prior[-1][4])
    if close <= 0 or prior_close <= 0:
        return None

    volume = sum(_n(row[5]) for row in session)
    price_volume = sum(
        ((_n(row[2]) + _n(row[3]) + _n(row[4])) / 3.0) * _n(row[5])
        for row in session
    )
    vwap = price_volume / volume if volume else close
    change_pct = (close / prior_close - 1.0) * 100.0
    vwap_distance_pct = (close / vwap - 1.0) * 100.0 if vwap else 0.0

    current_slot = _slot(last[0])
    peer_volume = [
        _n(row[5])
        for row in prior
        if _slot(row[0]) == current_slot and _n(row[5]) > 0
    ][-10:]
    fallback_volume = [
        _n(row[5])
        for row in rows[max(0, index - 24) : index]
        if _n(row[5]) > 0
    ]
    volume_base = peer_volume if len(peer_volume) >= 3 else fallback_volume
    average_volume = sum(volume_base) / len(volume_base) if volume_base else 0.0
    volume_ratio = _n(last[5]) / average_volume if average_volume else 1.0

    trend_base = _n(session[-6][4]) if len(session) > 5 else _n(session[0][1])
    trend_return_pct = (close / trend_base - 1.0) * 100.0 if trend_base else 0.0

    true_ranges = []
    previous_close = prior_close
    for row in session:
        high = _n(row[2])
        low = _n(row[3])
        candle_close = _n(row[4])
        true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(max(0.0, true_range))
        if candle_close > 0:
            previous_close = candle_close
    recent_ranges = true_ranges[-6:]
    atr = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 0.0
    atr_pct = atr / close * 100.0 if close and atr else 0.0
    prior_ranges = true_ranges[-6:-1]
    prior_range_mean = sum(prior_ranges) / len(prior_ranges) if prior_ranges else atr
    volatility_expansion = true_ranges[-1] / prior_range_mean if true_ranges and prior_range_mean else 1.0

    return {
        "symbol": symbol,
        "sector": SECTOR.get(symbol, "OTHER"),
        "change_pct": change_pct,
        "above_vwap": close >= vwap,
        "volume_ratio": volume_ratio,
        "vwap_distance_pct": vwap_distance_pct,
        "trend_return_pct": trend_return_pct,
        "atr_pct": atr_pct,
        "volatility_expansion": volatility_expansion,
    }


def _build_continuous_context(all_rows):
    nifty = all_rows.get("NIFTY", [])
    bank = all_rows.get("BANKNIFTY", [])
    maps = {
        symbol: {
            key: index
            for index, row in enumerate(all_rows.get(symbol, []))
            if (key := _minute_key(row[0]))
        }
        for symbol in SYMBOLS
    }
    observations = []

    for index, candle in enumerate(nifty):
        key = _minute_key(candle[0])
        if not key:
            continue
        slot = key[-5:]
        if slot < CONTEXT_WINDOW_START or slot > CONTEXT_WINDOW_END:
            continue

        stocks = []
        for symbol in STOCKS:
            stock_index = maps[symbol].get(key)
            if stock_index is None:
                continue
            summary = _continuous_symbol_at(symbol, all_rows.get(symbol, []), stock_index)
            if summary:
                stocks.append(summary)
        if len(stocks) < 24:
            continue

        nifty_summary = _continuous_symbol_at("NIFTY", nifty, index)
        bank_index = maps["BANKNIFTY"].get(key)
        bank_summary = (
            _continuous_symbol_at("BANKNIFTY", bank, bank_index)
            if bank_index is not None
            else None
        )
        if not nifty_summary or not bank_summary:
            continue

        advances = sum(row["change_pct"] > 0.05 for row in stocks)
        declines = sum(row["change_pct"] < -0.05 for row in stocks)
        above_vwap = sum(row["above_vwap"] for row in stocks)
        breadth_score = (
            ((advances - declines) / len(stocks) * 50.0)
            + ((above_vwap / len(stocks)) - 0.5) * 50.0
        )
        flow_score = (
            sum(
                (
                    1 if row["change_pct"] > 0.05
                    else -1 if row["change_pct"] < -0.05
                    else 0
                )
                * min(row["volume_ratio"], 2.0)
                for row in stocks
            )
            / len(stocks)
            * 25.0
        )
        observations.append({
            "ts": key,
            "stocks_available": len(stocks),
            "breadth_score": breadth_score,
            "advance_share": advances / len(stocks),
            "above_vwap_share": above_vwap / len(stocks),
            "flow_score": flow_score,
            "nifty": nifty_summary,
            "bank": bank_summary,
        })
    return observations


def _feature_vector(context, direction):
    sign = 1.0 if str(direction) == "LONG" else -1.0
    nifty = context.get("nifty") or {}
    bank = context.get("bank") or {}
    nifty_atr = max(_n(nifty.get("atr_pct")), 0.01)
    bank_atr = max(_n(bank.get("atr_pct")), 0.01)
    return {
        "breadth_alignment": round(sign * _n(context.get("breadth_score")) / 50.0, 6),
        "flow_alignment": round(sign * _n(context.get("flow_score")) / 25.0, 6),
        "nifty_vwap_alignment": round(
            _clip(sign * _n(nifty.get("vwap_distance_pct")) / nifty_atr, -5.0, 5.0),
            6,
        ),
        "bank_vwap_alignment": round(
            _clip(sign * _n(bank.get("vwap_distance_pct")) / bank_atr, -5.0, 5.0),
            6,
        ),
        "nifty_trend_alignment": round(
            _clip(sign * _n(nifty.get("trend_return_pct")) / nifty_atr, -5.0, 5.0),
            6,
        ),
        "bank_trend_alignment": round(
            _clip(sign * _n(bank.get("trend_return_pct")) / bank_atr, -5.0, 5.0),
            6,
        ),
        "volatility_expansion": round(
            _clip(
                (
                    _n(nifty.get("volatility_expansion"), 1.0)
                    + _n(bank.get("volatility_expansion"), 1.0)
                )
                / 2.0,
                0.0,
                5.0,
            ),
            6,
        ),
    }


async def run_market_brain_v7_observations(
    provider,
    start_date: str,
    end_date: str,
    role: str,
):
    frozen = FROZEN_BLOCKS.get((start_date, end_date))
    normalized_role = str(role).upper()
    if not frozen:
        raise ValueError("v7 accepts only its seven frozen development/holdout blocks")
    block_id, expected_role = frozen
    if normalized_role != expected_role:
        raise ValueError(f"{block_id} is frozen as role {expected_role}")

    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    context_start = start - timedelta(days=5)
    context_data = {}
    context_errors = []

    async def fetch_one(symbol):
        try:
            rows = await _historical(provider, symbol, "15m", context_start, end)
            return symbol, rows, None
        except Exception as exc:
            return symbol, [], f"{exc.__class__.__name__}: {exc}"

    for offset in range(0, len(SYMBOLS), 4):
        batch = await asyncio.gather(
            *(fetch_one(symbol) for symbol in SYMBOLS[offset : offset + 4])
        )
        for symbol, rows, error in batch:
            context_data[symbol] = rows
            if error:
                context_errors.append({"symbol": symbol, "error": error})
        await asyncio.sleep(0.15)

    context_observations = _build_continuous_context(context_data)
    context_by_ts = {
        row["ts"]: row
        for row in context_observations
        if start_date <= row["ts"][:10] <= end_date
    }

    backtest = await run_backtest(
        provider,
        SETUP_SYMBOLS,
        start_date,
        end_date,
        1.5,
        None,
    )
    trades = backtest.get("trades", [])
    eligible = [
        trade
        for trade in trades
        if _inside_context_window(trade.get("timestamp"))
    ]
    observations = []
    unmatched = []

    for trade in eligible:
        key = _minute_key(trade.get("timestamp"))
        context = context_by_ts.get(key) if key else None
        if not context:
            unmatched.append({
                "symbol": trade.get("symbol"),
                "timestamp": trade.get("timestamp"),
                "normalized_key": key,
            })
            continue
        r_multiple = _n(trade.get("r_multiple"))
        observations.append({
            "observation_id": f"{trade.get('symbol')}|{key}",
            "block_id": block_id,
            "role": expected_role,
            "symbol": trade.get("symbol"),
            "timestamp": trade.get("timestamp"),
            "direction": trade.get("direction"),
            "r_multiple": round(r_multiple, 4),
            "win": 1 if r_multiple > 0 else 0,
            "features": _feature_vector(context, trade.get("direction")),
        })

    return {
        "mode": "ALPHAPILOT_MARKET_BRAIN_V7_OBSERVATIONS",
        "research_only": True,
        "production_rules_changed": False,
        "protocol_revision": "v7-frozen-2026-08-25",
        "block_id": block_id,
        "role": expected_role,
        "start_date": start_date,
        "end_date": end_date,
        "setup_trades": len(trades),
        "eligible_setup_trades": len(eligible),
        "matched_observations": len(observations),
        "match_rate_pct": round(len(observations) / len(eligible) * 100.0, 1) if eligible else 0.0,
        "overall": _summary(observations),
        "feature_names": list(FEATURE_NAMES),
        "observations": observations,
        "context_errors": context_errors,
        "backtest_errors": backtest.get("errors", []),
        "match_diagnostics": {
            "timestamp_key": "Asia/Kolkata minute",
            "eligible_context_window": f"{CONTEXT_WINDOW_START}-{CONTEXT_WINDOW_END}",
            "unmatched_count": len(unmatched),
            "unmatched_samples": unmatched[:5],
        },
        "limitations": [
            "This endpoint collects frozen v7 observations; it does not fit or tune a model.",
            "Features use only information available at the setup timestamp.",
            "P&L uses underlying-price R rather than historical option-premium execution.",
            "Production remains unchanged.",
        ],
    }


def _sanitize_observations(rows, role):
    cleaned = []
    seen = set()
    for raw in rows:
        timestamp = str(raw.get("timestamp", ""))
        key = _minute_key(timestamp)
        if not key:
            raise ValueError(f"{role} observation has an invalid timestamp")
        day = key[:10]
        if role == "DEVELOPMENT" and not (DEVELOPMENT_START <= day <= DEVELOPMENT_END):
            raise ValueError("development observations must be inside the frozen development dates")
        if role == "HOLDOUT" and not (HOLDOUT_START <= day <= HOLDOUT_END):
            raise ValueError("holdout observations must be inside the locked H-1 dates")
        observation_id = str(raw.get("observation_id") or f"{raw.get('symbol')}|{key}")
        if observation_id in seen:
            continue
        seen.add(observation_id)

        raw_features = raw.get("features") or {}
        features = []
        for name in FEATURE_NAMES:
            value = _n(raw_features.get(name), float("nan"))
            if not math.isfinite(value):
                raise ValueError(f"{role} observation is missing finite feature {name}")
            features.append(value)
        r_multiple = _n(raw.get("r_multiple"), float("nan"))
        if not math.isfinite(r_multiple):
            raise ValueError(f"{role} observation has an invalid R multiple")
        cleaned.append({
            "observation_id": observation_id,
            "block_id": raw.get("block_id"),
            "symbol": raw.get("symbol"),
            "timestamp": timestamp,
            "direction": raw.get("direction"),
            "x": features,
            "r_multiple": r_multiple,
            "win": 1 if r_multiple > 0 else 0,
        })
    return cleaned


def _standardizer(rows):
    width = len(FEATURE_NAMES)
    means = [
        sum(row["x"][column] for row in rows) / len(rows)
        for column in range(width)
    ]
    scales = []
    for column, mean in enumerate(means):
        variance = sum(
            (row["x"][column] - mean) ** 2
            for row in rows
        ) / len(rows)
        scale = math.sqrt(variance)
        scales.append(scale if scale > 1e-12 else 1.0)
    return means, scales


def _standardize(rows, means, scales):
    return [
        [
            (row["x"][column] - means[column]) / scales[column]
            for column in range(len(FEATURE_NAMES))
        ]
        for row in rows
    ]


def _sigmoid(value):
    if value >= 0:
        inverse = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + inverse)
    exponent = math.exp(max(value, -60.0))
    return exponent / (1.0 + exponent)


def _fit_logistic(rows):
    means, scales = _standardizer(rows)
    matrix = _standardize(rows, means, scales)
    labels = [row["win"] for row in rows]
    weights = [0.0] * (len(FEATURE_NAMES) + 1)
    size = len(rows)

    for _ in range(MODEL_ITERATIONS):
        gradients = [0.0] * len(weights)
        for values, label in zip(matrix, labels):
            score = weights[0] + sum(
                weight * value
                for weight, value in zip(weights[1:], values)
            )
            error = _sigmoid(score) - label
            gradients[0] += error
            for column, value in enumerate(values, start=1):
                gradients[column] += error * value
        gradients[0] /= size
        for column in range(1, len(weights)):
            gradients[column] = (
                gradients[column] / size
                + MODEL_L2 * weights[column]
            )
        for column in range(len(weights)):
            weights[column] -= MODEL_LEARNING_RATE * gradients[column]
    return means, scales, weights


def _predict(rows, means, scales, weights):
    matrix = _standardize(rows, means, scales)
    probabilities = []
    for values in matrix:
        score = weights[0] + sum(
            weight * value
            for weight, value in zip(weights[1:], values)
        )
        probabilities.append(_sigmoid(score))
    return probabilities


def _brier(labels, probabilities):
    return sum(
        (probability - label) ** 2
        for label, probability in zip(labels, probabilities)
    ) / len(labels)


def _log_loss(labels, probabilities):
    total = 0.0
    for label, probability in zip(labels, probabilities):
        clipped = _clip(probability, 1e-12, 1.0 - 1e-12)
        total -= label * math.log(clipped) + (1 - label) * math.log(1.0 - clipped)
    return total / len(labels)


def _auc(labels, probabilities):
    positives = [
        probability
        for label, probability in zip(labels, probabilities)
        if label == 1
    ]
    negatives = [
        probability
        for label, probability in zip(labels, probabilities)
        if label == 0
    ]
    if not positives or not negatives:
        return None
    score = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                score += 1.0
            elif positive == negative:
                score += 0.5
    return score / (len(positives) * len(negatives))


def _improvement_pct(baseline, model):
    if baseline <= 0:
        return 0.0
    return (baseline - model) / baseline * 100.0


def _probability_bands(rows, probabilities):
    ranked = sorted(
        zip(rows, probabilities),
        key=lambda item: item[1],
    )
    first_cut = len(ranked) // 3
    second_cut = len(ranked) * 2 // 3
    partitions = (
        ("LOW", ranked[:first_cut]),
        ("MID", ranked[first_cut:second_cut]),
        ("HIGH", ranked[second_cut:]),
    )
    bands = []
    for name, sample in partitions:
        trades = len(sample)
        wins = sum(row["win"] for row, _ in sample)
        total_r = sum(row["r_multiple"] for row, _ in sample)
        bands.append({
            "band": name,
            "trades": trades,
            "avg_probability": round(
                sum(probability for _, probability in sample) / trades,
                4,
            ) if trades else 0.0,
            "win_rate": round(wins / trades * 100.0, 1) if trades else 0.0,
            "avg_r": round(total_r / trades, 3) if trades else 0.0,
            "total_r": round(total_r, 2),
        })
    return bands


def evaluate_market_brain_v7(development_rows, holdout_rows):
    development = _sanitize_observations(development_rows, "DEVELOPMENT")
    holdout = _sanitize_observations(holdout_rows, "HOLDOUT")
    if not development:
        raise ValueError("v7 requires development observations")
    if not holdout:
        raise ValueError("v7 requires locked holdout observations")
    development_wins = sum(row["win"] for row in development)
    if development_wins in {0, len(development)}:
        raise ValueError("v7 development sample must contain wins and non-wins")

    means, scales, weights = _fit_logistic(development)
    probabilities = _predict(holdout, means, scales, weights)
    labels = [row["win"] for row in holdout]
    holdout_wins = sum(labels)
    holdout_non_wins = len(holdout) - holdout_wins
    baseline_probability = development_wins / len(development)
    baseline_probabilities = [baseline_probability] * len(holdout)

    model_brier = _brier(labels, probabilities)
    baseline_brier = _brier(labels, baseline_probabilities)
    model_log_loss = _log_loss(labels, probabilities)
    baseline_log_loss = _log_loss(labels, baseline_probabilities)
    auc = _auc(labels, probabilities)
    bands = _probability_bands(holdout, probabilities)
    low = bands[0]
    high = bands[-1]
    win_spread = high["win_rate"] - low["win_rate"]
    r_spread = high["avg_r"] - low["avg_r"]

    sample_gate = (
        len(holdout) >= MIN_HOLDOUT_OBS
        and holdout_wins >= MIN_HOLDOUT_CLASS
        and holdout_non_wins >= MIN_HOLDOUT_CLASS
        and all(band["trades"] >= MIN_BAND_OBS for band in bands)
    )
    gates = {
        "sample_gate": sample_gate,
        "brier_improvement_at_least_10pct": _improvement_pct(baseline_brier, model_brier) >= 10.0,
        "log_loss_improvement_at_least_5pct": _improvement_pct(baseline_log_loss, model_log_loss) >= 5.0,
        "auc_at_least_0_60": auc is not None and auc >= 0.60,
        "high_minus_low_win_rate_at_least_10pp": win_spread >= 10.0,
        "high_minus_low_avg_r_at_least_0_20": r_spread >= 0.20,
        "high_avg_r_at_least_0_10": high["avg_r"] >= 0.10,
    }
    if not sample_gate:
        decision = "INSUFFICIENT_HOLDOUT_SAMPLE"
    elif all(gates.values()):
        decision = "VALIDATED_CONTINUOUS_REGIME_QUALITY_CANDIDATE"
    else:
        decision = "NO_VALIDATED_CONTINUOUS_REGIME_QUALITY_EDGE"

    predictions = []
    for row, probability in zip(holdout, probabilities):
        predictions.append({
            "observation_id": row["observation_id"],
            "block_id": row.get("block_id"),
            "symbol": row.get("symbol"),
            "timestamp": row["timestamp"],
            "direction": row.get("direction"),
            "actual_win": row["win"],
            "r_multiple": round(row["r_multiple"], 4),
            "probability": round(probability, 6),
        })

    return {
        "mode": "ALPHAPILOT_MARKET_BRAIN_V7_CONTINUOUS_REGIME_QUALITY",
        "research_only": True,
        "production_rules_changed": False,
        "protocol_revision": "v7-frozen-2026-08-25",
        "decision": decision,
        "development": {
            "observations": len(development),
            "wins": development_wins,
            "win_rate": round(development_wins / len(development) * 100.0, 1),
            "period": f"{DEVELOPMENT_START} to {DEVELOPMENT_END}",
        },
        "holdout": {
            "observations": len(holdout),
            "wins": holdout_wins,
            "non_wins": holdout_non_wins,
            "win_rate": round(holdout_wins / len(holdout) * 100.0, 1),
            "period": f"{HOLDOUT_START} to {HOLDOUT_END}",
        },
        "model": {
            "type": "L2_REGULARIZED_LOGISTIC_REGRESSION",
            "feature_names": list(FEATURE_NAMES),
            "iterations": MODEL_ITERATIONS,
            "learning_rate": MODEL_LEARNING_RATE,
            "l2": MODEL_L2,
            "intercept": round(weights[0], 8),
            "standardized_coefficients": {
                name: round(weights[index + 1], 8)
                for index, name in enumerate(FEATURE_NAMES)
            },
            "training_means": {
                name: round(means[index], 8)
                for index, name in enumerate(FEATURE_NAMES)
            },
            "training_scales": {
                name: round(scales[index], 8)
                for index, name in enumerate(FEATURE_NAMES)
            },
        },
        "probability_metrics": {
            "baseline_probability": round(baseline_probability, 6),
            "model_brier": round(model_brier, 6),
            "baseline_brier": round(baseline_brier, 6),
            "brier_improvement_pct": round(
                _improvement_pct(baseline_brier, model_brier),
                2,
            ),
            "model_log_loss": round(model_log_loss, 6),
            "baseline_log_loss": round(baseline_log_loss, 6),
            "log_loss_improvement_pct": round(
                _improvement_pct(baseline_log_loss, model_log_loss),
                2,
            ),
            "roc_auc": round(auc, 6) if auc is not None else None,
        },
        "probability_bands": bands,
        "economic_spreads": {
            "high_minus_low_win_rate_pp": round(win_spread, 1),
            "high_minus_low_avg_r": round(r_spread, 3),
        },
        "acceptance_gates": gates,
        "fixed_acceptance_rules": {
            "min_holdout_observations": MIN_HOLDOUT_OBS,
            "min_wins_and_non_wins": MIN_HOLDOUT_CLASS,
            "min_probability_band_observations": MIN_BAND_OBS,
            "min_brier_improvement_pct": 10.0,
            "min_log_loss_improvement_pct": 5.0,
            "min_roc_auc": 0.60,
            "min_high_low_win_rate_spread_pp": 10.0,
            "min_high_low_avg_r_spread": 0.20,
            "min_high_band_avg_r": 0.10,
        },
        "predictions": predictions,
        "limitations": [
            "The holdout is scored once without refitting or threshold search.",
            "Underlying-price R is used instead of historical option-premium execution.",
            "A validated result remains research-only until a later unseen confirmation sample.",
            "Production remains unchanged.",
        ],
    }
