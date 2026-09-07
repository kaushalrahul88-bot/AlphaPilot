# V4 change rationale

The fresh current-expiry diagnostic showed two code-level semantic defects in frozen V3. This note records the rationale without using outcome performance to fit any parameter.

## Alpha semantics

The existing technical engine interprets `alpha_score` around 50 and already uses 42/58 as bearish/bullish strength boundaries. V3 incorrectly normalized raw alpha around zero (`alpha/3`) and used `abs(alpha)/3` for expansion pressure. V4 therefore maps `(alpha-50)/8`, clipped to [-1, 1]. This is a semantic correction based on the producer's existing scale, not an outcome-fit transform.

## Direction conflict

V3 computed both signed magnitude and margin from the same difference between positive and negative evidence, so the margin gate was not independent. V4 uses `abs(P-N)/(P+N)` as weighted dominance. The architectural minimum is 0.20, which corresponds to a 60/40 evidence split. This is a scale-free conflict definition, not a value selected from backtest performance.

V3 is retained unchanged as a frozen benchmark.
