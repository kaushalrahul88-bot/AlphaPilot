# F&O Market Brain V5 — Holdout A Losing-Trade Forensics

Development analysis only. These observations were made **after** V5 Historical Holdout A failed and therefore may inform V6, but they are not proof of edge and cannot be used to promote V5 or V6.

Source dataset: `FNO_MARKET_BRAIN_V5_HOLDOUT_A_2026-09-07`  
Frozen V5 Brain: `a5a39253c1aa79f6663cd11b41f7d6607b96af90`  
Primary horizon: 90 minutes

## Failed holdout recap

- 63 unique actionable V5 theses after de-duplication.
- 32 correct, 31 incorrect; 50.7937% 90m accuracy.
- Mean 90m directional return +0.056134%; median +0.011108%.
- V5 failed sample sufficiency, directional accuracy, temporal robustness and cross-stock robustness.
- HDFCBANK: 20 theses, 40.0% accuracy, -0.153900% mean 90m return.
- INFY: 14 theses, 71.4286%, +0.443530%.
- MARUTI: 13 theses, 15.3846%, -0.310925%.
- BHARTIARTL: 16 theses, 75.0%, +0.277941%.
- LONG: 38 theses, 55.2632%, +0.182611%.
- SHORT: 25 theses, 44.0%, -0.136111%.

## What actually traded

All 63 effective actionable V5 theses were `TRANSITION`. `PULLBACK_REENTRY` produced zero effective actionable theses.

There were 285 V5 `PULLBACK_REENTRY` candidates blocked by `MATURE_TREND_CHASE_BLOCKED`. If those candidate sides are scored descriptively at the frozen 90m horizon, they produce only 47.3684% accuracy and -0.121757% mean directional return. Therefore simply loosening the pullback gate is not supported by this development sample.

## Main transition failure found

V5 transition readiness required five-minute confirmation plus **either** participation or proximity to a structural boundary. That OR gate was too permissive.

Among the 63 effective V5 transitions:

| V5 evidence state | N | 90m accuracy | Mean 90m directional return |
|---|---:|---:|---:|
| Structure-only | 19 | 42.1053% | -0.1708% |
| Volume-only | 14 | 35.7143% | -0.2429% |
| Both volume + structure | 30 | 63.3333% | +0.3394% |

This is development evidence only, but it indicates that one weak cue was often insufficient to distinguish a genuine transition from transient noise.

## Stronger evidence package explored for V6

Using only semantic boundaries that already existed before Holdout A outcomes were known, the following post-hoc development hypothesis was examined:

- retain the V5 15m RANGE + momentum/relative-strength transition candidate;
- retain V5 5m directional confirmation;
- require either:
  1. volume expansion >=1.25x on **both** 5m and 15m; or
  2. aligned 15m price-action family confirmation at the existing +/-1 semantic boundary;
- structural proximity remains diagnostic but cannot promote a transition by itself.

On Holdout A development outcomes this condition selects 27 of the 63 V5 theses:

- 21/27 correct = 77.7778% 90m accuracy;
- +0.4424% mean 90m directional return;
- June: 11 theses, 72.7273%, +0.2874%;
- July: 16 theses, 81.25%, +0.5490%;
- LONG: 17 theses, 88.2353%, +0.6740%;
- SHORT: 10 theses, 60.0%, +0.0486%.

By stock in this **development-only** slice:

- BHARTIARTL: 9, 88.8889%, +0.5016%.
- HDFCBANK: 6, 50.0%, -0.0101%.
- INFY: 9, 88.8889%, +0.7337%.
- MARUTI: 3, 66.6667%, +0.2960%.

These values must not be presented as V6 performance. The rule was derived after seeing Holdout A outcomes and requires a completely new unseen historical holdout.

## What was not promoted into V6

- No stock-specific exclusion. HDFCBANK/MARUTI weakness is treated as a generalization warning, not a reason to remove those names after the fact.
- No LONG-only rule, despite LONG looking stronger in Holdout A.
- No fitted numeric threshold from Holdout A returns.
- No peer-breadth threshold. Peer alignment was directionally useful but not robust enough across stocks to justify a new gate.
- No reactivation of pullback/re-entry.
- No forward test.

## V6 design implication

V6 should be a new, frozen Brain version rather than an in-place V5 edit. Holdout A is now permanently development data. V6 must be tested on a disjoint historical holdout before any strategic-edge claim, and a failed new holdout cannot be reused after further revisions.
