# F&O Market Brain V7 freeze

Protocol: `FNO_MARKET_BRAIN_V7_PERSISTENCE_CONFIRMATION_2026-09-08`

Development source: `FNO_MARKET_BRAIN_V6_HOLDOUT_B_2026-09-08`

## Research status

V7 is a development revision created only after V6 Historical Holdout B failed its frozen strategic-edge gates. Holdout B outcomes are development data for V7 and cannot be reused as V7 promotion evidence.

V7 is not a forward-tested or live model. It remains underlying-only and derivative-free.

## Frozen decision rule

V7 first computes the unchanged V6 decision. V7 can only filter an already-actionable V6 `TRANSITION`; it cannot create a LONG or SHORT that V6 rejected.

For a V6-actionable side:

- **Aligned completed 1h price action:** allow the V6 transition.
- **Opposed completed 1h price action:** block the transition.
- **Neutral completed 1h price action:** allow only if all local confluence conditions are true:
  1. V6 dual-timeframe volume expansion is true (`5m >= 1.25x` and `15m >= 1.25x`),
  2. V6 aligned 15m price-action family is true,
  3. the directional 5m or 15m structure boundary is within `0.75 ATR`.

The price-action family boundary remains the existing V5 engine semantic of +/-1. No new numerical threshold is fitted to Holdout B.

## Properties that are frozen

- LONG and SHORT use the same rule.
- V7 is a strict subset of V6 actionables.
- Existing V5/V6 phase, transition, relative-strength, 5m confirmation and counter-regime safeguards remain unchanged.
- Existing V6 transition evidence is not recomputed from outcomes.
- Alpha score is not used as a gate.
- No stock-specific, sector-specific or time-of-day filter is added.
- No change is made to the 90-minute primary research horizon.
- No option-chain, option premium, IV, Greeks, option OI or futures inputs are read.
- No future bar or realized outcome is read by the Brain.
- Thesis de-duplication remains supported and session reset remains required in replay datasets.

## Promotion rule

The next evaluation must use a newly frozen historical holdout that is disjoint from:

- V3 development/current-expiry rows,
- V5 Historical Holdout A,
- V6 Historical Holdout B.

Because V7 is materially more selective than V6, the next holdout must be sized before outcomes so that the existing minimum of 80 unique actionable theses is realistically reachable without lowering the frozen accuracy/significance standards.

The core strategic-edge standards remain unchanged:

- primary horizon: 90 minutes;
- non-flat accuracy >= 58%;
- exact one-sided binomial p-value < 0.05;
- positive mean 90m directional return;
- non-negative median 90m directional return;
- positive temporal performance in each frozen window;
- cross-stock robustness and concentration controls;
- no diagnostic split can override a failed primary gate.

Only a separate pre-outcome holdout protocol may define the exact fresh stocks, dates and sample-sufficiency counts needed for that new dataset.

## Prohibited interpretation

The favorable V7 counterfactual observed on Holdout B is not evidence that V7 has an edge. It is development evidence used to define this new version. V7 must pass fresh unseen historical evidence before forward testing can even be discussed.
