# F&O Market Brain V5 — Historical Holdout A Protocol

Protocol: `FNO_MARKET_BRAIN_V5_HOLDOUT_A_2026-09-07`

Frozen Brain commit: `a5a39253c1aa79f6663cd11b41f7d6607b96af90`

## Purpose

This is **historical backtesting only**, not forward testing. Its purpose is to determine whether the phase-first V5 architecture developed from the V3 losing-trade forensics has repeatable directional edge on earlier, disjoint market periods.

The V3 720-row August/September sample is development data and is excluded from Holdout A outcomes. V1/V2 August windows are also excluded.

## Frozen temporal holdout

Use the same four cross-sector underlyings so this first holdout isolates temporal generalisation:

- `HDFCBANK` — private banking
- `INFY` — information technology
- `MARUTI` — passenger automobiles
- `BHARTIARTL` — telecommunications

Two disjoint historical windows are frozen before any V5 outcome is resolved:

1. `2026-06-01` through `2026-06-12` — inside the June stock-F&O cycle ending `2026-06-30`.
2. `2026-07-01` through `2026-07-14` — inside the July stock-F&O cycle ending `2026-07-28`.

Actual NSE trading sessions are discovered from the common four-stock cash tape. No date may be dropped after outcomes are seen except for a documented source-data integrity failure that affects the complete session according to a rule fixed before scoring.

## Frozen observations

- 20 deterministic pseudo-random clicks per completed trading session.
- Click pool: `09:30` through `14:00` IST in five-minute increments.
- The same click timestamps are used for all four stocks on a session.
- Inputs: completed historical `5m`, `15m`, and `1h` cash candles; candle-derived market/peer context; and point-in-time news/events genuinely public by the click.
- No options, option premiums, OI, IV, Greeks, or futures are decision inputs.
- V5 decisions are computed once and frozen before outcomes.
- `ThesisTracker` resets at each stock/session boundary. Repeated emissions of the same unchanged thesis are suppressed and do not count as additional actionable observations.
- Exact five-minute future tapes and SHA-256 hashes are frozen for deterministic later resolution.

## Frozen outcome definition

The **primary horizon is 90 minutes**. This is selected before Holdout A outcomes are inspected because V5 is intended to detect an intraday directional move with enough time for a transition/pullback thesis to develop; it is not selected by comparing Holdout A horizons.

For an actionable V5 thesis:

- LONG directional return = underlying return from click price to the 90-minute endpoint.
- SHORT directional return = negative of that underlying return.
- `correct` means directional return > 0.
- `incorrect` means directional return < 0.
- exact zero is `flat` and excluded from the binomial accuracy denominator but retained in return statistics.

The existing `15m`, `30m`, `60m`, and `EOD` horizons are reported only as secondary/descriptive diagnostics. They cannot replace 90m as the primary Holdout A pass horizon after results are seen.

## Pre-registered edge gates

Holdout A passes only if **all** primary gates below are satisfied on de-duplicated actionable theses:

1. **Sample sufficiency**
   - at least 80 unique actionable theses overall;
   - at least 20 unique actionable theses in each frozen window;
   - at least three of the four stocks have at least 10 actionable theses.
2. **Directional accuracy**
   - non-flat 90m directional accuracy >= 58%; and
   - exact one-sided binomial test versus 50% has `p < 0.05`.
3. **Directional payoff**
   - mean 90m directional return > 0%; and
   - median 90m directional return >= 0%.
4. **Temporal robustness**
   - each frozen window separately has 90m accuracy > 50%; and
   - each frozen window separately has positive mean 90m directional return.
5. **Cross-stock robustness**
   - among stocks with at least 10 actionable theses, at least three have positive mean 90m directional return; and
   - no stock with at least 10 actionable theses has 90m directional accuracy below 45%.
6. **Concentration control**
   - no single stock contributes more than 45% of actionable theses; and
   - no single session contributes more than 20% of actionable theses.

Wilson confidence intervals, setup-type splits (`TRANSITION`, `PULLBACK_REENTRY`), LONG/SHORT splits, MFE/MAE, and all secondary horizons must be reported, but they do not override a failed primary gate.

## Interpretation boundary

Passing Holdout A establishes only a **historical temporal edge candidate**. It is not enough to start forward testing.

If Holdout A passes, V5 remains unchanged and proceeds to a second, independently frozen **cross-sectional historical Holdout B** using different stocks. Only if the same frozen V5 logic survives that second historical validation may we describe the evidence as a repeatable historical strategic edge sufficient to discuss forward testing.

If Holdout A fails, its outcomes become development evidence. V5 may be analysed and a new version created, but the failed Holdout A cannot then be reused as that revised version's promotion holdout. No post-hoc threshold retuning of frozen V5 is allowed.
