# CRUDE_CURRENT_MIND_NO_NEWS_V1 — frozen research specification

Freeze point: after exact parity with the validated `CRUDE_LONG_ONLY_SHADOW_V1` on the preregistered August random-click schedule.

## Frozen action rule

- BUY only when the original Crude Brain-A rule is true:
  - market structure = `UPTREND`;
  - 15-minute return > 0;
  - price above EMA20;
  - price above EMA50.
- Every other state = WAIT.
- SELL is not permitted in this V1 no-news brain.

## Timing

Groww five-minute candle timestamps are bar starts. OHLC from a bar becomes usable only at `bar_start + 5 minutes`.

## Decision inputs

Only the four frozen technical decision inputs above can determine BUY versus WAIT.

The following may be journaled as annotations but cannot change the action:
- session return;
- session range position;
- VWAP location;
- relative volume;
- ATR/volatility;
- OI state when available.

## Explicit exclusions

- News, headlines, EIA data, geopolitical state, OPEC events, sanctions, supply/demand stories and any other news-derived field are forbidden from the no-news decision.
- No option-premium translation is part of this brain.
- No live execution is enabled.
- No filter or threshold may be added in response to August results.

## Evidence already completed before freeze

- technical Brain-A baseline;
- Brain-B comparison on chronological holdout (Brain B failed and was not tuned);
- development-only attribution;
- preregistered long-only shadow;
- independent Aug 3-14 historical validation;
- Aug 3-31 day-by-day stability;
- deterministic 20-click/session replay: 420/420 clicks;
- Current Mind parity replay: 420/420 clicks, zero action mismatches.

The parity replay is an assembly check, not new strategy evidence.

## Next validation

The frozen brain may now be evaluated on a different historical period whose outcomes were not used to define or modify this rule. Results from that period cannot be used to modify V1 and then be re-scored on the same period.

Only after the no-news baseline is fully reviewed may a separate point-in-time news shadow be attached. The news comparison must preserve the frozen no-news brain and use identical timestamps for both arms.
