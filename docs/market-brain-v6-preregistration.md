# Market Brain v6: Dynamic Context Preregistration

Status: frozen before implementation and before viewing any v6 result.

## Research question

Do setup-time changes or persistence in breadth, flow, market leadership, and NIFTY/BANKNIFTY alignment change the underlying-price expectancy of AlphaPilot's existing historical scanner setups?

This is a research-only experiment. It does not alter production strategy, ranking, risk, stops, sizing, or execution gates.

## Frozen setup population

- Existing historical scanner technical, multi-timeframe, and safety logic.
- Symbols: RELIANCE, HDFCBANK, ICICIBANK, SBIN, TCS, INFY, TATASTEEL, and MARUTI.
- One trade per symbol per day maximum, as already enforced by the backtest.
- Setup timestamps from 09:45 through 14:30 Asia/Kolkata only.
- Outcome: existing underlying-price R multiple with the current 1.5R backtest target.
- Baseline: all matched setups in the same direction, LONG or SHORT, within each block.

## Frozen dynamic features

Every feature uses only the context observation at setup time and the immediately preceding 15-minute observation from the same trading day. No future observation and no previous-day carry are allowed.

1. `breadthImpulse`
   - `IMPROVING` when breadth moves upward on the ordinal scale BROAD_RISK_OFF (-1), MIXED (0), BROAD_RISK_ON (+1).
   - `DETERIORATING` when it moves downward.
   - `STABLE` otherwise.
2. `flowImpulse`
   - `IMPROVING` when flow moves upward on the ordinal scale SELLING_PRESSURE (-1), BALANCED (0), BUYING_PRESSURE (+1).
   - `DETERIORATING` when it moves downward.
   - `STABLE` otherwise.
3. `leaderImpulse`
   - `BROADENING` when leadership moves upward on the ordinal scale 0-2_LEADERS (0), 3-5_LEADERS (1), 6+_LEADERS (2).
   - `NARROWING` when it moves downward.
   - `STABLE` otherwise.
4. `indexAlignment`
   - Index phase sign is +1 for ALIGNED_UP or RECOVERY, -1 for ALIGNED_DOWN or FADE, and 0 for MIXED.
   - `BULLISH_ALIGNED` when NIFTY and BANKNIFTY both have sign +1.
   - `BEARISH_ALIGNED` when both have sign -1.
   - `DIVERGENT_OR_MIXED` otherwise.
5. `breadthPersistence`
   - `PERSISTENT_RISK_ON` when current and prior breadth are both BROAD_RISK_ON.
   - `PERSISTENT_RISK_OFF` when both are BROAD_RISK_OFF.
   - `MIXED_OR_CHANGING` otherwise.
6. `flowPersistence`
   - `PERSISTENT_BUYING` when current and prior flow are both BUYING_PRESSURE.
   - `PERSISTENT_SELLING` when both are SELLING_PRESSURE.
   - `BALANCED_OR_CHANGING` otherwise.

The first usable context observation of each trading day has no prior same-day observation and is excluded from v6 dynamic matching.

## Frozen effect gates

For each direction, feature, and state:

- Minimum group size: 12 matched trades.
- `BOOST`: group average R minus same-direction baseline average R is at least +0.20R, and group win rate minus baseline win rate is at least +8 percentage points.
- `DRAG`: both differences are at most -0.20R and -8 percentage points respectively.
- `MIXED`: the group meets the minimum sample but not both BOOST or both DRAG gates.
- `LOW_SAMPLE`: fewer than 12 trades.

## Frozen evaluation blocks

| Block | Start | End |
|---|---|---|
| S-0A | 2026-05-25 | 2026-06-05 |
| S-0B | 2026-06-08 | 2026-06-19 |
| S-0C | 2026-06-22 | 2026-07-03 |
| S-1 | 2026-07-06 | 2026-07-17 |
| S-2 | 2026-07-20 | 2026-07-31 |
| S-3 | 2026-08-03 | 2026-08-10 |

## Frozen replication decision

An effect replicates only when the exact same direction, feature, and state is BOOST in at least three of the six blocks with no qualifying DRAG block, or DRAG in at least three blocks with no qualifying BOOST block.

- If no effect replicates, the v6 decision is `NO_REPLICATED_DYNAMIC_CONTEXT_EFFECT`.
- A replicated effect becomes only a frozen candidate for a new untouched validation sample. It never becomes a production rule automatically.

## Exclusions and interpretation limits

- Static archetype/context combinations tested in v5 are not re-opened.
- News and cross-asset features are excluded because timestamp-aligned historical feeds are not available.
- Results measure conditional expectancy of existing scanner setups, not unconditional market direction.
- Multiple hypotheses are reported transparently; replication across blocks, rather than a single-block result, is the decision criterion.
