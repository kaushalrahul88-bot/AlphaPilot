# Crude Experiment 001 contract

Purpose: establish the first no-news Crude Oil Brain baseline before any historical news is allowed into decisions.

Frozen decision rule:
- BUY: UPTREND + positive 15m return + price above EMA20 and EMA50.
- SELL: DOWNTREND + negative 15m return + price below EMA20 and EMA50.
- Otherwise NO_TRADE.

Primary evaluation horizon: 60 minutes.
Round-trip research cost: 4 bps.
Sampling: every third completed 5-minute bar after a 50-bar warm-up.
Chronological report: 70% development / 30% untouched holdout, in addition to the descriptive full-sample score.

News policy: forbidden. Headlines, event labels, EIA values, geopolitical state and any outcome-derived news annotation may not influence Experiment 001.

Timing policy: a Groww 5-minute candle timestamp is the bar start and its OHLC is visible only at timestamp + 5 minutes. Forward-path labels begin with the next bar.

This contract must not be changed in response to Experiment 001 performance. Any later Brain B candidate is compared against this frozen baseline on the same chronological holdout.
