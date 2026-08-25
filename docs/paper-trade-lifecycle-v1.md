# Paper Trade Lifecycle v1

Paper Trade Lifecycle v1 converts an approved deterministic paper-risk decision into an exact option-contract paper position and marks it using live Groww option-chain LTP observations.

## Endpoints

- `POST /v1/paper-trades/open` fetches the exact strike/expiry/type premium, refreshes the risk request at that premium, and returns either `OPENED_PAPER` or `OPEN_BLOCKED`.
- `POST /v1/paper-trades/mark` fetches a new exact-contract premium and returns an open mark or a stop, target, or explicit manual close.

Neither endpoint contains or calls a broker order operation.

## Hard boundaries

- Only `PAPER` risk mode can open a lifecycle position.
- Exact symbol, expiry, strike, option type, and lot size are mandatory.
- Actual lifecycle endpoints require Groww data identified as live.
- The originating risk evaluation must be no more than 120 seconds from the opening observation.
- The risk engine is re-run at the observed premium; the earlier entry value is not trusted.
- Position size remains whole-lot and cannot exceed the refreshed risk decision.
- State identity, quantity, price geometry, and initial risk are checked before every mark.
- Live execution and order-endpoint flags are always false.

## P&L and risk projections

Estimated round-trip costs are deducted from unrealized and realized P&L. Open risk is conservatively retained at the initial defined risk until the position closes. A closed result emits a compact verified-history projection for the discipline engine.

## Persistence limitation

The API is stateless. The frontend stores lifecycle state in the browser and submits it for deterministic checking and marking. This is suitable for v1 paper validation but is not a tamper-evident regulatory record. Server-side signed persistence is required before any future execution-capable phase.

## Price limitation

Groww option-chain LTP is used for paper marking. LTP is not a guaranteed executable bid/ask fill and therefore cannot by itself validate real slippage or fill quality.
