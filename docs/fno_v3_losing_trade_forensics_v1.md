# V3 Losing-Trade Forensics V1

Protocol: `FNO_V3_LOSING_TRADE_FORENSICS_V1_2026-09-07`

## Research boundary

This is a **development-only historical forensic** of the already-completed V3 backtest. It does not run a forward test, does not read options/futures, and does not mutate frozen V3 or V4. Any rule discovered here is a hypothesis for a future Brain version and must be confirmed on separate historical data before it can be called a strategic edge.

## Baseline

- 720 observations; 517 actionable (71.8056%).
- LONG 281, SHORT 236, NO_TRADE 203.
- EOD actionable accuracy 52.0309%.
- Mean EOD directional return -0.119656%.
- Mean 90m directional return -0.034727%.

## What the losing trades show

### 1. V3 direction confidence is anti-calibrated at the top end

The highest absolute direction-score quartile produced only **38.76%** EOD accuracy. Increasing confluence did not increase correctness. This is consistent with correlated momentum evidence (EMA/VWAP/MACD/structure/engine signal) being counted as independent confirmation and with the Brain entering after a move was already mature.

### 2. Alpha semantics contaminated both stages

Across all 720 rows, V3's raw alpha handling contributed a constant **+3.6 bullish direction score** and **+2.2 expansion pressure** because the legacy 0-100 alpha scale was treated as signed around zero. This is the semantic defect already corrected in frozen V4. V3 consequently marked **720/720** observations expansion-ready. Removing only that technical-pressure term would have reduced readiness to 531/720, while the V4 semantic fix alone still leaves 713/720 ready. So the expansion gate itself remains too permissive.

### 3. The largest LONG failure mode is higher-timeframe conflict

V3 LONG calls with opposing 1h market structure:
- 134 observations
- 44 wins / 90 losses
- **32.84%** EOD accuracy
- **-0.780%** mean EOD directional return

When 1h structure did not oppose the LONG:
- 147 observations
- 80 wins / 67 losses
- **54.42%** accuracy
- **+0.048%** mean EOD directional return

A future Brain should not let several correlated lower-timeframe bullish indicators overwhelm a materially bearish higher-timeframe state without a specific reversal/transition setup.

### 4. Trend labels appear late rather than predictive

For SHORT decisions:
- when 15m was already `DOWNTREND`: 175 observations, 54.65% EOD accuracy, -0.022% mean EOD directional return.
- while 15m was still `RANGE`: 61 observations, **85.0%** EOD accuracy, **+0.646%** mean EOD directional return.

For LONG, chasing a 15m `UPTREND` was especially poor: 143 observations, 29.37% accuracy, -0.730% mean EOD directional return.

This suggests the current Brain rewards *already-established trend state* too heavily. The stronger hypothesis is to identify the **transition/setup phase before structure fully flips**, rather than chase a mature trend label.

### 5. Repeated clicks multiply one bad thesis

The top five stock-session-direction loss clusters account for **26.6%** of all 244 actionable losses; the top ten account for **48.8%**. A particularly clear example was BHARTIARTL on 31 Aug: repeated LONG calls occurred while 15m was bullish but 1h remained `DOWNTREND` / `WATCH_SHORT`, creating many copies of the same wrong thesis.

A future setup engine needs trade/thesis state: an unchanged setup should be returned as the same active thesis, and an invalidated thesis should not be immediately recreated until materially new evidence appears.

## Strongest development candidate found

A simple, interpretable SHORT condition emerged:

1. V3 direction is SHORT.
2. 15m market structure is still `RANGE`.
3. 15m MACD supports SHORT.
4. Relative strength vs NIFTY supports SHORT.

On the same development backtest it produced:
- **46 observations**
- **41 wins / 4 losses / 1 flat**
- 60m mean directional return **+0.122%**
- 90m mean directional return **+0.218%**
- EOD accuracy **91.11%**
- EOD mean directional return **+0.800%**
- present across all four stocks and seven sessions.

This is **not yet a proven edge** because it was discovered after inspecting these 720 outcomes. It is, however, a much better strategy hypothesis than simply increasing V3's score threshold.

## V5 research direction

1. De-correlate evidence families so multiple momentum-derived features cannot create false confidence.
2. Model market **phase** (`compression/range -> transition -> mature trend -> exhaustion`) instead of equating mature trend with better entry.
3. Treat 1h as regime/context and require an explicit reversal model before taking a conflicting LONG/SHORT.
4. Require local directional momentum plus relative-strength confirmation for transition setups.
5. Replace the almost-always-true expansion gate with a point-in-time rolling/regime-relative readiness rank.
6. Add stateful thesis/cooldown/invalidation logic so repeated clicks do not multiply the same trade.
7. Keep LONG and SHORT strategy families separable; this sample shows materially different behavior and does not justify forcing symmetric rules.

The next permitted research step is **historical backtesting only**: encode these as a new V5 research candidate and test it on separate earlier historical periods. Do not forward-test or live-test until a repeatable historical edge survives that process.
