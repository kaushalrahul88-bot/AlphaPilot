# F&O Market Brain V6 — Evidence-Strength Transition Freeze

Protocol: `FNO_MARKET_BRAIN_V6_EVIDENCE_TRANSITION_2026-09-08`

## Development boundary

V6 is an outcome-driven revision created after V5 Historical Holdout A failed. Holdout A is development evidence only and is permanently disqualified as V6 promotion evidence.

No forward testing is authorized by this freeze.

## Frozen V6 logic

V6 preserves V5's phase-first structure and existing semantic boundaries. It changes only transition evidence strength and pullback promotion:

1. 15m must remain `RANGE`.
2. 15m momentum must align with point-in-time relative strength versus NIFTY, using the existing 0.02% deadband.
3. 5m directional confirmation is required exactly as in V5: aligned momentum plus at least one aligned trend/structure/price-action family.
4. A transition then requires at least one strong evidence package:
   - **DUAL_VOLUME**: both completed 5m and 15m volume ratios are >=1.25x; or
   - **15M_PRICE_ACTION**: completed 15m price-action family vote is aligned at the existing +/-1 confirmation boundary.
5. Nearby support/resistance within 0.75 ATR remains diagnostic only; it cannot independently promote a trade.
6. A single timeframe with >=1.25x volume cannot independently promote a trade.
7. Counter-1h transitions retain the V5 explicit reversal package and opposing-news veto.
8. `PULLBACK_REENTRY` is disabled pending separate unseen evidence.
9. LONG and SHORT rules remain symmetric. No stock-specific rule exists.
10. Alpha score is not a direction or evidence gate.

## Thesis de-duplication

V6 retains `ThesisTracker`, but the V6 thesis identity includes the evidence mode. `DUAL_VOLUME`, `15M_PRICE_ACTION`, and `DUAL_VOLUME_AND_15M_PRICE_ACTION` are distinct evidence states while repeated unchanged emissions remain suppressed.

## Safety boundary

V6 decision inputs are completed underlying 5m/15m/1h candles plus point-in-time context. It does not read future bars, options, option prices, IV, Greeks, option OI, futures, or realized outcomes. It cannot execute live trades.

## Required next validation

The next test must be a newly frozen historical holdout that is disjoint from V3 development and V5 Holdout A development. Dates, stocks, click schedule, primary horizon, sample sufficiency, accuracy/payoff, temporal/cross-stock robustness and concentration gates must be frozen before any V6 outcome is resolved.

A V6 result on Holdout A may be reported only as development diagnostics and never as validation.
