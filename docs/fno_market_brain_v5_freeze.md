# F&O Market Brain V5 — development freeze note

Protocol: `FNO_MARKET_BRAIN_V5_PHASE_TRANSITION_2026-09-07`

## Research boundary

V5 is a new development candidate created after the completed V3 losing-trade forensics. The V3 720-row current-expiry sample is therefore **development/in-sample evidence only** for V5. V5 must not be presented as validated on those observations, and the discovered high-performing V3 SHORT transition subset is explicitly **not** a proven edge.

No V5 historical holdout outcomes, forward observations, option data, futures data, or live execution are used by this module or its CI workflow. V3 and V4 remain unchanged.

## What changed architecturally

V5 replaces the V3/V4 pattern of adding many correlated indicator contributions with a phase-first decision process:

1. **1h regime** — bullish, bearish, or mixed, using the existing technical engine's `market_structure` plus its already-established trend-family confirmation boundary.
2. **15m phase** — range, transition, developing/countertrend, mature trend, or exhaustion.
3. **Setup type** — only two initial setup families are admitted:
   - `TRANSITION`: 15m is still RANGE while 15m momentum and point-in-time relative strength agree, followed by 5m confirmation and an independent expansion cue.
   - `PULLBACK_REENTRY`: a mature 15m/1h trend is not chased; a trade becomes eligible only after 5m returns to RANGE/pullback state and trend+momentum recover near support/resistance with participation.
4. **Higher-timeframe control** — a lower-timeframe transition opposing the 1h regime is blocked unless an explicit symmetric reversal package is present. Opposing point-in-time news blocks that counter-regime exception.
5. **Thesis de-duplication** — actionable decisions carry a deterministic thesis key and state signature. `ThesisTracker` suppresses repeated emissions of an unchanged thesis; evaluators should reset the tracker at session boundaries.

## Boundaries

V5 does not fit new numerical thresholds to V3 returns. It reuses existing engine semantics already present before V5 research:

- trend-family confirmation: `+/-8`
- momentum-family confirmation: `+/-4`
- structure-family confirmation: `+/-7`
- moderate volume participation: `1.25x`
- normal volume participation: `0.80x`
- support/resistance proximity: `0.75 ATR`
- relative-strength deadband: `0.02%`

The legacy `alpha_score` is not used as a V5 direction or expansion gate. This prevents a return of the V3 alpha-scale semantic defect and avoids treating a composite score as independent evidence.

## Validation sequence

After this Brain is merged and frozen, the next permitted research step is **historical holdout backtesting on disjoint older periods** that were not used to design V5. Before those outcomes are resolved, the holdout dates/universe/click schedule/outcome horizon and pass/fail criteria must be frozen.

Forward testing remains blocked until a strategic edge survives independent historical validation with adequate sample size, robustness across stocks/sessions/directions, and no post-hoc threshold retuning.
