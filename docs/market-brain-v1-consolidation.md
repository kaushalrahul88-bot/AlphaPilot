# Market Brain V1 Consolidation & Validation

## Purpose

Freeze the current candidate architecture long enough to determine whether it understands the market better than simpler alternatives. This phase is not a search for another indicator or another news layer.

## Core validation dimensions

1. **Point-in-time integrity** — every feature must be knowable at the decision timestamp; sparse sessions are excluded rather than reconstructed with future information.
2. **Direction reading** — compare the frozen technical baseline (Brain A) with the richer candidate (Brain B) at fixed 30/60/120 minute same-session horizons. Directional accuracy is descriptive and is not sufficient to establish option profitability.
3. **Abstention quality** — explicitly audit WAIT/NO_TRADE decisions, especially those followed by large moves. A system that avoids losses by refusing every difficult opportunity is not considered intelligent.
4. **Experience/Memory** — historical analogue retrieval must be walk-forward. Future experiences cannot be visible to a query.
5. **Incremental value** — richer context must be compared with deliberately simpler variants. A component earns production influence only through pre-registered out-of-sample evidence, not because it produces a persuasive explanation.
6. **Execution separation** — underlying market thesis, decision to trade, and CE/PE contract expression remain separate. Futures/spot data may inform context; futures P&L and synthetic option P&L cannot validate the options strategy.

## Current expected blocker

The existing Copper direction audit skips `NO_TRADE` observations. Therefore it cannot measure whether AlphaPilot correctly abstained or failed to perceive a large move. The first consolidation scorecard is expected to remain `NOT_READY` until a frozen point-in-time abstention audit exists.

Required abstention outputs:

- number of WAIT/NO_TRADE observations;
- subsequent move magnitude at fixed same-session horizons;
- count/rate of abstentions followed by a pre-registered large move;
- opportunity cost distribution;
- descriptive failure attribution (`DATA_GAP`, `PERCEPTION_GAP`, `INTERPRETATION_GAP`, `STRATEGY_GAP`, `UNFORESEEABLE_OR_NEW_INFORMATION`);
- repeated missed-move hypotheses may enter research, but cannot auto-change the Market Brain.

## News during consolidation

The Market News Reaction architecture remains shadow evidence. No additional news interpretation layer should be promoted merely from the seven currently classifiable Copper events. Larger-sample testing can continue, but news cannot manufacture direction or override price/structure.

## Promotion rule

Completing all measurement dimensions does **not** automatically promote the candidate brain. It only means the validation evidence is complete enough for a pre-registered out-of-sample decision. Any future promotion must be based on frozen rules, holdout data, process quality, calibration, abstention quality, risk-adjusted trade results and stability across regimes/periods.

## Non-negotiable guardrails

- No look-ahead or post-click information in feature construction.
- Do not tune the candidate using holdout outcomes.
- Do not treat overlapping observations as independent trades.
- Do not declare success from win rate or directional accuracy alone.
- Do not reward a model merely for increasing trade frequency.
- NO_TRADE followed by a large move is a learning case, not automatically a mistake and not automatically a success.
- Production trade rules remain unchanged throughout this consolidation phase.
