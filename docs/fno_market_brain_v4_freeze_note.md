# F&O Market Brain V4 freeze scope

Protocol: `FNO_MARKET_BRAIN_V4_2026-09-07`

V4 is a new outcome-blind Brain version. V3 remains frozen and unchanged.

This version is limited to semantic corrections established from code-level review:

- Interpret the legacy `alpha_score` on its actual 0-100-style scale centered at 50, with existing 42/58 bearish/bullish boundaries mapped to -1/+1.
- Replace V3's duplicate conflict margin (`abs(positive-negative)`, mathematically the same magnitude as the signed score) with an independent dominance ratio.
- Keep the two-stage expansion/direction architecture, existing raw architecture thresholds, point-in-time context, and derivative exclusions.

No historical V4 outcome replay, pass/fail scoring, threshold search, or parameter fitting was performed while implementing this version. Unit/CI checks are permitted; real evaluation criteria must be agreed before V4 is tested against future outcomes.
