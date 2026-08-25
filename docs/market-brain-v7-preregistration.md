# Market Brain v7: Continuous Regime Quality Preregistration

Status: frozen before implementation and before viewing any v7 result.

## Research question

Can a small, interpretable model of continuous setup-time market conditions produce better-calibrated win probabilities and a meaningful expectancy ordering for AlphaPilot's existing historical scanner setups?

Market Brain v3 through v6 closed categorical context paths without a replicated effect. v7 does not add another categorical feature cross and does not change any production rule.

## Frozen populations and split

- Setup engine: the existing historical scanner technical, multi-timeframe, and safety logic.
- Symbols: RELIANCE, HDFCBANK, ICICIBANK, SBIN, TCS, INFY, TATASTEEL, and MARUTI.
- Setup window: 09:45 through 14:30 Asia/Kolkata.
- Outcome: win when the existing underlying-price R multiple is greater than zero.
- Secondary economic outcome: the unchanged underlying-price R multiple.
- Development period: 25 May 2026 through 10 August 2026, collected in the six already frozen blocks S-0A through S-3.
- Locked v7 holdout: 11 August 2026 through 21 August 2026.
- The holdout must not be inspected, summarized, or used to alter features, model settings, or acceptance gates before the final model is fitted.

## Frozen continuous features

All features use only candles available at the setup timestamp. Directional measurements are multiplied by +1 for LONG and -1 for SHORT, so a positive value means alignment with the setup direction.

1. `breadth_alignment`
   - Continuous breadth score from the 30-stock proxy, combining advance/decline balance and share above session VWAP.
   - Scaled by 50 and signed to the setup direction.
2. `flow_alignment`
   - Continuous volume-weighted advance/decline pressure from the proxy.
   - Scaled by 25 and signed to the setup direction.
3. `nifty_vwap_alignment`
   - NIFTY distance from session VWAP divided by NIFTY six-bar 15-minute ATR, signed to setup direction and clipped to [-5, +5].
4. `bank_vwap_alignment`
   - BANKNIFTY distance from session VWAP divided by BANKNIFTY six-bar 15-minute ATR, signed and clipped identically.
5. `nifty_trend_alignment`
   - NIFTY return over the available lookback of up to six 15-minute candles divided by NIFTY ATR, signed and clipped to [-5, +5].
6. `bank_trend_alignment`
   - BANKNIFTY trend return normalized and signed identically.
7. `volatility_expansion`
   - Mean of NIFTY and BANKNIFTY current true range divided by their respective mean prior five intraday true ranges, clipped to [0, 5].
   - This feature is not direction-signed.

No static categorical state, news history, cross-asset history, symbol identity, clock time, setup alpha, setup R:R, or realized outcome-derived feature is included.

## Frozen model

- Model: one L2-regularized logistic regression implemented deterministically.
- All seven features are standardized using development-period means and population standard deviations only.
- Constant features use scale 1.
- Optimizer: full-batch gradient descent.
- Iterations: 1,200.
- Learning rate: 0.05.
- L2 coefficient: 0.20.
- Intercept is not regularized.
- The fitted model is applied once to the locked holdout with no refit, tuning, feature removal, threshold search, or calibration adjustment.

## Frozen baselines and metrics

The no-skill baseline predicts the development-period win rate for every holdout setup.

Primary probability metrics:

- Brier score
- Log loss
- ROC AUC

Secondary economic diagnostics:

- Three equal-count holdout probability bands: LOW, MID, and HIGH.
- Win rate, average predicted probability, average R, and total R for each band.
- HIGH-minus-LOW win-rate and average-R spreads.

## Frozen acceptance gates

The decision is `INSUFFICIENT_HOLDOUT_SAMPLE` unless:

- at least 36 holdout setups are matched;
- at least 10 holdout wins and 10 holdout non-wins exist; and
- every probability band contains at least 12 setups.

If the sample gate passes, v7 becomes `VALIDATED_CONTINUOUS_REGIME_QUALITY_CANDIDATE` only if all conditions hold:

- Brier score improves on the constant baseline by at least 10%;
- log loss improves on the constant baseline by at least 5%;
- holdout ROC AUC is at least 0.60;
- HIGH-band win rate exceeds LOW-band win rate by at least 10 percentage points;
- HIGH-band average R exceeds LOW-band average R by at least +0.20R; and
- HIGH-band average R is at least +0.10R.

Otherwise the decision is `NO_VALIDATED_CONTINUOUS_REGIME_QUALITY_EDGE`.

A validated result is only a frozen candidate for a later truly unseen confirmation sample. It never becomes a production gate automatically.

## Operational rules

- Development and holdout observations are collected in resumable blocks.
- Every completed block is checkpointed before the next begins.
- The locked holdout role and dates are stored in the experiment ledger.
- Model coefficients, standardization parameters, all holdout predictions, probability bands, metrics, and failed/passed gates are exported.
- Production strategy, ranking, permissions, vetoes, stops, trailing logic, partial exits, position sizing, portfolio risk, and execution gates remain unchanged.

## Limitations

- Outcomes use underlying-price R, not historical option-premium execution.
- Market breadth uses the frozen 30-stock proxy rather than the complete NSE universe.
- The locked holdout is short; the sample floor may force an insufficient-sample conclusion.
- Cross-asset and news history remain excluded until timestamp-aligned historical data exists.
