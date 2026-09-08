# F&O Market Brain V6 — Historical Holdout B forensics

Protocol evaluated: `FNO_MARKET_BRAIN_V6_HOLDOUT_B_EVALUATION_V1_2026-09-08`

Frozen Brain: `FNO_MARKET_BRAIN_V6_EVIDENCE_TRANSITION_2026-09-08`

This document records post-outcome development forensics only. Historical Holdout B has now been inspected and can never again be used as promotion evidence for V6 or any later Brain revision derived from these outcomes. No forward testing, options, futures, execution or capital logic is involved.

## Frozen Holdout B result

Holdout B contained 4,960 observations across 31 historical sessions, eight stocks and two pre-frozen windows. After thesis de-duplication V6 emitted 126 actionable decisions: 54 LONG and 72 SHORT.

Primary 90-minute result:

- 63 correct, 60 incorrect, 3 flat.
- Non-flat directional accuracy: 51.2195%.
- Mean directional return: +0.014770%.
- Median directional return: +0.002525%.
- 95% Wilson interval: 42.48% to 59.88%.
- Exact one-sided binomial p-value versus 50%: 0.4285.

The frozen gate result was FAIL. Sample sufficiency, payoff and concentration passed, but accuracy/significance, temporal robustness and cross-stock robustness failed.

Window split:

- April 2026: 73 actionable, 52.11% non-flat accuracy, -0.01637% mean 90m directional return.
- May 2026: 53 actionable, 50.00% non-flat accuracy, +0.05766% mean 90m directional return.

Side split:

- SHORT: 72 actionable, 54.93% accuracy, +0.03865% mean 90m directional return.
- LONG: 54 actionable, 46.15% accuracy, -0.01707% mean 90m directional return.

Important stock failures included DRREDDY at 42.31% and ULTRACEMCO at 38.46%, both below the frozen 45% stock floor. This prevents interpreting the small positive overall mean as a general directional edge.

## What the losing trades say

### 1. V6 evidence remained too permissive for persistence

V6 allowed a transition when the 5m confirmation was present and either:

- 5m and 15m volume were both expanded, or
- 15m price action aligned.

Those single-route groups were not reliable enough:

- `DUAL_VOLUME`: 60 actionable, 49.12% accuracy, -0.0280% mean 90m return.
- `15M_PRICE_ACTION`: 52 actionable, 50.00% accuracy, +0.0408% mean.
- Both evidence modes together: 14 actionable, 64.29% accuracy, +0.1014% mean.

The last group is development evidence only; its sample is small and it was discovered after outcomes.

### 2. Higher-timeframe price action carried information that V6 did not gate on

The 1h price-action family was not a V6 promotion requirement. Post-outcome diagnostics show:

- 1h price action aligned with the trade: 38 actionable, 63.89% accuracy, +0.1536% mean 90m return.
  - April: 66.67%, +0.1096% mean.
  - May: 58.33%, +0.2381% mean.
- 1h price action neutral: 80 actionable, 48.10% accuracy, -0.0256% mean.
- 1h price action opposed: 8 actionable, 25.00% accuracy, -0.2409% mean.

This is the strongest mechanism-level failure identified: V6 could emit a transition even when the completed 1h price-action family was neutral or directly opposed.

### 3. Structural location helps only as confirmation, not as a standalone trigger

A directional support/resistance boundary within 0.75 ATR was kept diagnostic-only in V6.

- Near boundary: 81 actionable, 55.13% accuracy, +0.0639% mean.
- Not near boundary: 45 actionable, 44.44% accuracy, -0.0737% mean.

The effect was not temporally stable enough to promote proximity by itself. This is consistent with the earlier V5 lesson that structural proximity must never be sufficient on its own.

### 4. The 90-minute losers were usually persistent failures, not merely tiny endpoint noise

Among the 60 incorrect 90m calls:

- mean MFE was +0.2231%, versus mean MAE -0.6678%;
- 68.3% reached at least +0.10% favorable excursion at some point, but only 11.7% were still directionally positive at 60 minutes;
- 93.3% suffered at least -0.25% adverse excursion within 90 minutes.

So many losing calls briefly moved in the predicted direction and then reversed decisively. The immediate research problem is therefore persistence/invalidation, not simply entry sign. The frozen 90m horizon is retained; it is not changed after seeing these outcomes.

### 5. We deliberately did not encode every apparent pattern

Post-outcome splits also showed weak early-session SHORT performance, stock-specific differences and LONG/SHORT asymmetry. None of these are promoted directly because doing so would stack multiple outcome-derived filters and raise overfitting risk.

No stock blacklist, side-specific rule, time-of-day rule, new numeric threshold or horizon change is introduced from Holdout B.

## Selected V7 development hypothesis

The narrow V7 hypothesis is **transition persistence confirmation**.

V7 is a strict subset of V6. It can never create a trade that V6 rejected.

For a V6-actionable transition:

1. If the completed 1h price-action family is aligned with the side, persistence is confirmed.
2. If the completed 1h price-action family is opposed, the transition is blocked.
3. If the completed 1h price-action family is neutral, the transition is allowed only when all three existing local semantics agree:
   - dual-timeframe volume expansion,
   - aligned 15m price action,
   - directional structural boundary within 0.75 ATR.

No new numeric boundary is fitted. All boundaries already existed in V5/V6.

A counterfactual application of this exact rule to development Holdout B retained 43 of 126 V6 actionables:

- 26 correct, 15 incorrect, 2 flat.
- 63.41% non-flat accuracy.
- +0.15093% mean 90m directional return.
- +0.09151% median 90m return.
- April: 65.38% accuracy, +0.10486% mean.
- May: 60.00% accuracy, +0.22868% mean.
- LONG: 61.54% accuracy, +0.12065% mean.
- SHORT: 64.29% accuracy, +0.16555% mean.

The 83 V6 actionables rejected by this counterfactual had 45.12% accuracy and -0.05577% mean 90m return.

These numbers are **not validation**. The candidate was selected after inspecting Holdout B, and its one-sided binomial p-value on this development sample is about 0.0586. V7 therefore requires a completely fresh unseen historical holdout with enough observations to meet the unchanged statistical edge criteria before it can be considered evidence of a strategic edge.

## Research status after this forensics pass

- V6: failed and remains frozen.
- Holdout B: development data only from this point onward.
- V7: development candidate only.
- Forward testing: blocked.
- Options/futures translation: blocked.
- Live execution: blocked.
- Next proof step: freeze V7, then freeze a new larger unseen historical holdout before resolving any outcomes.
