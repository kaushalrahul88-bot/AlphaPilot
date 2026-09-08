# F&O Market Brain V7 — Historical Holdout C protocol

Protocol: `FNO_MARKET_BRAIN_V7_HOLDOUT_C_2026-09-08`

Frozen Brain protocol: `FNO_MARKET_BRAIN_V7_PERSISTENCE_CONFIRMATION_2026-09-08`

Frozen Brain commit: `461053bb98ef60ffdad9f9bd34ce4a5e599016c3`

## Purpose

Historical Holdout C is the first promotion test for frozen V7. It is a historical backtest only, not a forward test. V7 was created after V6 Historical Holdout B outcomes were inspected, therefore every V3 development row, V5 Holdout A row and V6 Holdout B row is excluded from Holdout C promotion evidence.

The protocol, stock universe, calendar windows, click-generation rule, primary horizon and pass/fail gates are frozen before Holdout C outcomes are resolved.

## Frozen target universe

Twelve target stocks are used. None was a target stock in V3, V5 Holdout A, V6 Holdout B, or the original V1/V2 four-stock benchmark.

1. `ICICIBANK` — PRIVATE_BANKING
2. `TCS` — INFORMATION_TECHNOLOGY
3. `M&M` — AUTOMOBILES
4. `HINDUNILVR` — FMCG_CONSUMER
5. `CIPLA` — PHARMACEUTICALS
6. `HINDALCO` — METALS_ALUMINIUM
7. `NTPC` — POWER_GENERATION
8. `TITAN` — CONSUMER_DISCRETIONARY
9. `ASIANPAINT` — PAINTS_HOME_IMPROVEMENT
10. `ADANIPORTS` — PORTS_LOGISTICS
11. `COALINDIA` — COAL_MINING_ENERGY
12. `BRITANNIA` — FMCG_FOODS

Peer stocks may overlap earlier target universes because peers are point-in-time context only; no prior target decision or realized outcome is imported into Holdout C.

## Frozen historical windows

The windows are fixed calendar ranges. Actual scored sessions are the common NSE cash sessions present in every target stock's 5-minute tape; holidays are naturally absent and no session may be dropped because of an outcome.

- `JANUARY_2026`: 2026-01-05 through 2026-01-30
- `FEBRUARY_2026`: 2026-02-02 through 2026-02-27
- `MARCH_2026`: 2026-03-02 through 2026-03-27

These are deliberately disjoint from V5 Holdout A (June/July 2026), V6 Holdout B (April/May 2026), and V3 development/current-expiry rows (August/September 2026).

The study is underlying-only. Calendar windows are not altered around derivative expiries because no option or futures input is used by the Brain or outcome resolver.

## Frozen observations

For every completed common session:

- 20 deterministic pseudo-random clicks;
- clicks are sampled without replacement from 5-minute-aligned timestamps from 09:30 through 14:00 IST;
- the exact same 20 click timestamps are used for all twelve target stocks that session;
- the random seed is SHA-256 of `PROTOCOL_ID:YYYY-MM-DD`;
- `ThesisTracker` resets for each target stock at each session boundary;
- only the effective deduplicated V7 action is eligible as an actionable call.

No post-outcome click deletion, replacement or resampling is permitted.

## Point-in-time decision information

Allowed before each simulated click:

- completed 5m, 15m and 1h cash candles for the target stock;
- completed 15m NIFTY market context;
- completed 15m peer-basket context;
- static business/driver metadata;
- historical news/events whose frozen `effective_at` timestamp is no later than the click.

Groww timestamps are treated conservatively as candle-start timestamps, so the current unfinished interval is excluded.

Unknown intraday event publication times use the next NSE session open. Directional event labels use only information intrinsic to the publication; ambiguous or mixed events are `NEUTRAL`. No subsequent share-price reaction is used to label an event.

Forbidden decision inputs:

- future candles or realized returns;
- option-chain state, option prices, IV, Greeks or option OI;
- futures prices, basis or futures OI;
- V3/V5/V6 realized holdout outcomes;
- live execution state or capital allocation.

## Frozen future tape

The dataset builder freezes the complete in-window 5-minute cash tape for each target stock and records a SHA-256 hash before any outcome is resolved. The outcome evaluator must re-verify every hash before scoring.

## Frozen outcome mechanics

The established underlying replay path is retained unchanged:

- reference price = last fully completed 5m close at click time;
- Groww timestamp = candle start;
- future 5m bars satisfy `click_at <= bar_start` and `bar_start + 5m <= horizon_end`;
- primary horizon = **90 minutes**;
- descriptive horizons = 15m, 30m, 60m and EOD;
- EOD uses the last fully completed 5m bar by 15:30 IST;
- LONG directional return = raw underlying return;
- SHORT directional return = negative raw underlying return;
- zero return is flat and is excluded only from the directional-accuracy denominator;
- MFE and MAE are retained for diagnostics.

A missing 90-minute outcome for an actionable call is a data-integrity failure and cannot be silently removed from the denominator.

## Pre-registered strategic-edge gates

Holdout C passes only if **all** gates below pass. Diagnostics, subgroups or other horizons cannot override a failed primary gate.

### 1. Sample sufficiency

- at least 80 unique actionable V7 theses overall;
- at least 30 actionable theses in each of the three frozen windows;
- at least 6 of 12 stocks must have at least 8 actionable theses each.

If V7 is too selective to meet these counts, Holdout C fails sample sufficiency. The gates or windows are not enlarged after outcomes are inspected.

### 2. Primary directional accuracy and significance

At the 90-minute horizon:

- non-flat directional accuracy >= 58%;
- exact one-sided binomial test against 50% must have p < 0.05;
- report the 95% Wilson confidence interval.

### 3. Primary payoff

At 90 minutes:

- mean directional return > 0%;
- median directional return >= 0%.

### 4. Temporal robustness

Each of `JANUARY_2026`, `FEBRUARY_2026`, and `MARCH_2026` must independently have:

- non-flat 90m accuracy > 50%;
- positive mean 90m directional return.

### 5. Cross-stock robustness

Among stocks with at least 8 actionable theses:

- at least 5 must have positive mean 90m directional return;
- no eligible stock may have 90m non-flat accuracy below 45%.

### 6. Concentration control

- no single stock may contribute more than 30% of all actionable theses;
- no single session may contribute more than 15% of all actionable theses.

## Required reporting

The evaluator must report:

- observations and LONG/SHORT/NO_TRADE counts;
- actionable rate and thesis duplicate suppressions;
- 15m/30m/60m/90m/EOD accuracy and directional-return summaries;
- 90m Wilson interval and exact one-sided binomial p-value;
- MFE/MAE;
- splits by frozen window, stock, side, V7 persistence route and V6 evidence mode;
- stock/session concentration;
- each individual frozen gate and final pass/fail.

## Interpretation

A pass is evidence of a repeatable **historical** strategic edge candidate because V7 was frozen before this disjoint dataset was scored. It is still not permission to execute derivatives automatically.

A fail makes Holdout C development data. Any outcome-driven change must become V8 (or later) and must face another fresh unseen historical holdout. V7 itself remains frozen.

Forward testing, option/futures translation and live execution remain blocked until the historical strategic-edge requirement is satisfied.
