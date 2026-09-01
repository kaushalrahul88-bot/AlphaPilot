# Crude Oil Brain — no-news baseline path

This track intentionally mirrors the early Copper research path while keeping the Copper September forward-validation code untouched.

## Frozen order

1. Experiment 001: MCX Crude technical baseline (Brain A).
2. Brain B: structure/participation/regime candidate compared against frozen Brain A on the same chronological holdout.
3. Edge attribution and interaction audit.
4. Regime/day stability.
5. Day-by-day replay.
6. Random-click replay.
7. Assemble/freeze the no-news Crude Current Mind.
8. Only after the no-news baseline is frozen, attach point-in-time historical news intelligence.
9. Re-run the same dates and same click timestamps and compare no-news versus news-enabled decisions.
10. Option-premium translation is evaluated after underlying-direction evidence is acceptable.

## Experiment 001 restrictions

- NEWS is forbidden as a decision input.
- Groww 5-minute timestamps are treated as bar starts; OHLC becomes available at timestamp + 5 minutes.
- Forward returns are labels only and are attached after the snapshot is frozen.
- Exact-contract candles are required. Duplicate timestamps fail closed instead of mixing overlapping contracts.
- The first bounded backtest may use a currently listed contract's extended history, matching the starting-stage limitation of the early Copper work. It is not described as a continuous front-month test.
- Research only; no production rule or live execution change.
