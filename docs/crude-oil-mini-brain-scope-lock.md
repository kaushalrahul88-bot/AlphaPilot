# Crude Oil Mini Brain scope lock

## Product

- Tradable product: **MCX Crude Oil Mini options only**.
- Internal underlying family: `CRUDEOILM`.
- Allowed trade expressions: `BUY_CE`, `BUY_PE`, `WAIT`.
- Futures may be observed as market-reference data; futures are never an execution product.
- Regular `CRUDEOIL` contracts must never be silently substituted for `CRUDEOILM`.

## Development path

Reuse the proven AlphaPilot/Copper research skeleton, not Copper-specific learned rules:

1. Verify the current `CRUDEOILM` futures and option universe from provider data.
2. Build Crude Oil Mini perception + point-in-time historical memory with news disabled.
3. Run 20 outcome-blind deterministic clicks per complete session on the available current-contract window.
4. Emit `BUY_CE`, `BUY_PE`, or `WAIT` from information genuinely available at each click.
5. Where historical Mini option premium data is available, translate the setup into the nearest-expiry affordable Mini option and score the actual option path. Never synthesize missing option prices.
6. Freeze the no-news brain, exact candles, and exact click schedule.
7. Only then attach Crude-specific point-in-time News Intelligence and rerun the exact same data/clicks for an A/B comparison.

## Isolation

- Copper and Crude Oil Mini are separate brains and are not performance competitors.
- Copper forward-validation behavior is unchanged.
- Earlier regular-`CRUDEOIL` research is not training evidence for the Mini brain.
- No news is permitted in the initial Mini brain.
- No live execution is enabled.
