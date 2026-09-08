# F&O Market Brain V6 — Historical Holdout B Protocol

Protocol: `FNO_MARKET_BRAIN_V6_HOLDOUT_B_2026-09-08`

Frozen Brain protocol: `FNO_MARKET_BRAIN_V6_EVIDENCE_TRANSITION_2026-09-08`

Frozen Brain commit: `09fe4ca938b4b47c5d9cb8d33b91f321d5368ef6`

## Purpose

This is **historical backtesting only**, not forward testing. V5 Historical Holdout A failed and is now development data. V6 was created from that development evidence, so neither V3 development rows nor V5 Holdout A may be used as V6 promotion evidence.

Holdout B tests V6 on different primary stocks and earlier, disjoint monthly F&O periods. The objective is to test both temporal and cross-sectional generalisation while preserving the same underlying-only random-click mechanics.

## Frozen primary stocks

Eight liquid NSE underlyings are frozen before any V6 Holdout B outcome is resolved:

- `RELIANCE` — diversified energy / consumer / digital
- `TATASTEEL` — steel
- `ITC` — FMCG / cigarettes / agri
- `BAJFINANCE` — non-bank financial services
- `LT` — engineering / construction
- `DRREDDY` — pharmaceuticals
- `ULTRACEMCO` — cement
- `POWERGRID` — power transmission

None of these eight was a primary stock in V3 development or V5 Holdout A. Some may previously have appeared only as market/peer context; that does not make them primary-outcome development rows and will be disclosed in the final report.

## Frozen historical windows

Two disjoint windows are frozen:

1. `2026-04-01` through `2026-04-24`, inside the April stock-F&O cycle ending `2026-04-28`.
2. `2026-05-04` through `2026-05-22`, inside the May stock-F&O cycle ending `2026-05-26`.

Actual NSE sessions are discovered from the common eight-stock cash tape. No complete session may be dropped after outcomes are seen except under a pre-existing source-data integrity rule affecting the whole common session.

## Frozen observations

- 20 deterministic pseudo-random clicks per completed common trading session.
- Click pool: `09:30` through `14:00` IST, five-minute aligned.
- The same click timestamps are used for all eight stocks on a session.
- Inputs: completed historical `5m`, `15m`, and `1h` cash candles; candle-derived NIFTY/peer context; and point-in-time events genuinely public by the simulated click.
- Unknown intraday event publication time is treated as usable only from the next NSE session open.
- No options, option premiums, OI, IV, Greeks, futures, future candles, or realized outcomes may influence a V6 decision.
- V6 decisions are computed once and frozen before outcomes.
- `ThesisTracker` resets at each stock/session boundary; repeated unchanged actionable thesis emissions are suppressed and do not count as independent actionable observations.
- Exact five-minute future tapes and SHA-256 hashes are frozen for deterministic later scoring.

## Frozen outcome definition

The **primary horizon remains 90 minutes**, preserving the pre-outcome V5 validation horizon rather than selecting a better-looking V6 horizon.

For an effective actionable thesis:

- LONG directional return = underlying return from the last completed 5m close at the click to the 90-minute endpoint.
- SHORT directional return = negative of that underlying return.
- Correct: directional return > 0.
- Incorrect: directional return < 0.
- Exact zero: flat; excluded only from the binomial accuracy denominator and retained in return statistics.

The `15m`, `30m`, `60m`, and `EOD` horizons are descriptive only and cannot replace 90m after results are known. MFE/MAE are also descriptive.

## Pre-registered V6 Holdout B edge gates

Holdout B passes only if **all** of the following are satisfied on effective de-duplicated V6 theses:

1. **Sample sufficiency**
   - at least **80** unique actionable theses overall;
   - at least **30** unique actionable theses in each frozen window;
   - at least **six of eight** stocks have at least **8** actionable theses.
2. **Directional accuracy**
   - non-flat 90m accuracy >= **58%**; and
   - exact one-sided binomial test versus 50% has `p < 0.05`.
3. **Directional payoff**
   - mean 90m directional return > 0%; and
   - median 90m directional return >= 0%.
4. **Temporal robustness**
   - each frozen window separately has 90m accuracy > 50%; and
   - each frozen window separately has positive mean 90m directional return.
5. **Cross-stock robustness**
   - among stocks with at least 8 actionable theses, at least **five** have positive mean 90m directional return; and
   - no stock with at least 8 actionable theses has 90m accuracy below **45%**.
6. **Concentration control**
   - no single stock contributes more than **30%** of actionable theses; and
   - no single session contributes more than **15%** of actionable theses.

Wilson confidence interval, LONG/SHORT splits, evidence-mode splits (`DUAL_VOLUME`, `15M_PRICE_ACTION`, `DUAL_VOLUME_AND_15M_PRICE_ACTION`), stock/window/session splits, MFE/MAE, and all secondary horizons must be reported. Diagnostics cannot override a failed primary gate.

## Interpretation boundary

Passing this holdout would establish only a **new historical edge candidate for V6**. It does **not** authorize forward testing by itself. Because V6 was materially developed from a failed holdout, a further independently frozen historical confirmation sample should be required before discussing forward testing.

If Holdout B fails, its outcomes become development evidence. Any change becomes V7 or later, and Holdout B cannot be reused as promotion evidence for that revised Brain.
