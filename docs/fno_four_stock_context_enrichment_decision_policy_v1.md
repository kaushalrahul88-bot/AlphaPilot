# Enrichment Decision Policy V1 — Frozen Before Outcome Review

This policy is intentionally simple and outcome-agnostic.

## Components

- Technical vote: 5m = 1, 15m = 2, 1h = 2; BUY positive, SELL negative.
- NIFTY 60m momentum: +1 / 0 / -1 outside a +/-0.15% deadband.
- Sector peer-basket mean 60m momentum: +1 / 0 / -1 outside a +/-0.15% deadband.
- Stock relative strength versus NIFTY: +1 / 0 / -1 outside a +/-0.10% deadband.
- BANKNIFTY 60m momentum for SBIN only: +1 / 0 / -1 outside a +/-0.15% deadband.
- Audited event direction: bounded to +/-2; ambiguous events are zero.

## Action overlay

- Missing context: preserve baseline action; never create a trade.
- Existing LONG: veto to NO_TRADE only if context score <= -3.
- Existing SHORT: veto to NO_TRADE only if context score >= +3.
- Existing NO_TRADE: promote to LONG only if technical vote >= +3 and context score >= +4.
- Existing NO_TRADE: promote to SHORT only if technical vote <= -3 and context score <= -4.

These thresholds must not be changed after viewing the V1 enriched outcomes. Any future changes require a new protocol version and a separate validation sample.
