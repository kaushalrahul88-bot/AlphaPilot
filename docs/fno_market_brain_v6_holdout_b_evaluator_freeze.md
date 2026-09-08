# F&O Market Brain V6 — Historical Holdout B Evaluator Freeze

Evaluator protocol: `FNO_MARKET_BRAIN_V6_HOLDOUT_B_EVALUATION_V1_2026-09-08`

Source protocol: `FNO_MARKET_BRAIN_V6_HOLDOUT_B_2026-09-08`

Frozen Brain commit: `09fe4ca938b4b47c5d9cb8d33b91f321d5368ef6`

Frozen Holdout B infrastructure commit: `7505d57fe609d0c7bd64da10af3233a48ad4f0c0`

## Freeze boundary

This evaluator is defined and unit-tested **before Holdout B outcome scoring**. It does not recompute V6 decisions and it cannot change the stock universe, historical windows, click sample, thesis de-duplication, primary horizon, or pass gates.

The evaluator first verifies the source protocol, Brain protocol/commit, eight-stock universe, April/May windows, 20 common unique clicks per session, outcome-blind safety flags, and every frozen 5m tape hash. An actionable thesis with missing 90-minute outcome data is treated as source-data integrity failure rather than silently removed from the denominator.

## Frozen outcome mechanics

The evaluator reuses `fno_underlying_random_replay_v1.resolve_underlying_path` unchanged:

- reference = last completed 5m close at click;
- Groww candle timestamps are candle-start timestamps;
- only future 5m bars beginning at/after the click and fully complete by the horizon are eligible;
- LONG directional return = underlying return;
- SHORT directional return = negative underlying return;
- 90m is primary;
- 15m, 30m, 60m and EOD are descriptive only;
- exact-zero return is retained in payoff statistics but excluded from the binomial accuracy denominator;
- MFE/MAE are descriptive.

## Frozen pass gates

All must pass:

1. Sample sufficiency: >=80 effective actionable theses overall; >=30 in each window; >=6/8 stocks with >=8 actionable theses.
2. Directional accuracy: non-flat 90m accuracy >=58% and exact one-sided binomial p<0.05 versus 50%.
3. Directional payoff: mean 90m directional return >0 and median >=0.
4. Temporal robustness: each window accuracy >50% and mean 90m directional return >0.
5. Cross-stock robustness: among stocks with >=8 actionable theses, >=5 have positive mean 90m return and none has accuracy <45%.
6. Concentration: no stock >30% and no session >15% of actionable theses.

The evaluator reports 95% Wilson accuracy interval, LONG/SHORT, evidence-mode, stock, window and session splits, all horizons, and MFE/MAE. Diagnostics cannot override a failed primary gate.

## Safety

Historical backtesting only. No options, futures, forward testing, live execution, or capital allocation. If Holdout B fails, its outcomes become development evidence and any Brain change must be V7 or later on another unseen historical holdout.
