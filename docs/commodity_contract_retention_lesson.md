# Commodity contract retention lesson

This note records a data-provenance lesson for the shared Commodity Brain. It is not trading alpha and must never vote on direction.

## Observed Groww / MCX behavior

- While `COPPER31AUG26FUT` (`MCX-COPPER-31Aug26-FUT`) was active, AlphaPilot successfully retrieved and stored 3,318 five-minute candles for 2026-08-03 through 2026-08-28.
- On 2026-08-31 AlphaPilot intentionally stopped redundant scheduled storage of reconstructible commodity candles because Groww historical retrieval had tested positive.
- After the 2026-08-31 contract expired, a direct retest using that exact saved contract identity returned zero historical rows.
- As a control, the active `COPPER30SEP26FUT` returned 174 five-minute candles for each of 2026-09-01 through 2026-09-04, 696 rows total.

## Revised shared policy

Historical retrievability while a commodity futures contract is active must not be assumed to persist after expiry. AlphaPilot therefore avoids continuous redundant full-life storage during normal contract life but preserves a bounded, exact-contract replay archive before expiry.

The archive must preserve `trading_symbol`, `groww_symbol`, expiry and completed candle timestamps. It remains separate from immutable prospective first-seen observations. Archive time is not historical availability time. A missing expired contract must never be silently replaced with the next contract; replay must be partial or abstain unless an explicit cross-contract methodology is preregistered.

This policy is data readiness only. It has no Direction Brain vote, no confidence vote, no Current Mind effect, no option-expression effect and no broker-execution effect.
