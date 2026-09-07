# F&O Four-Stock Context Enrichment V1

Protocol: `FNO_FOUR_STOCK_CONTEXT_ENRICHMENT_V1_2026-09-07`

## Frozen experiment

- Logical stocks: ONGC, LTIM (current cash-provider alias LTM), SBIN, SUNPHARMA.
- Baseline: validated 1,600-observation candle-only replay.
- Same 20 sessions per stock, same 20 deterministic clicks per session, same future tape.
- Enrichment is shadow/research only.

## Point-in-time inputs

1. Existing stock 5m/15m/1h technical state from the baseline.
2. NIFTY 15m cash context for all four stocks.
3. BANKNIFTY 15m cash context for SBIN.
4. Frozen peer-basket 15m cash context by sector.
5. Stock relative strength versus NIFTY over 60 minutes.
6. Audited event archive. An event with no independently verified intraday public timestamp is delayed to the next NSE session open.

## Leakage controls

- Completed candles only.
- `effective_at <= click_at` for every event.
- Future prices/outcomes are never features.
- The validated baseline future path is reused rather than refetched.
- Decision thresholds are protocol constants frozen before enriched outcomes are observed.
- Missing required context fails closed and cannot create a trade.

## Explicit exclusions

No option chain, option premium, option OI, IV, Greeks, Futures signals, execution, orders, or capital are used. Options translation remains a later independent layer.
