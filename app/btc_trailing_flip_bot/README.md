# BTC/USDT Trailing-Flip Bot

Isolated AlphaPilot component for research/backtesting of the requested continuous BTC/USDT futures strategy.

## Strategy
- Initial direction: 5-minute EMA 9 vs EMA 21 only.
- LONG: reference is the highest price since entry; stop = reference x (1 - trail).
- SHORT: reference is the lowest price since entry; stop = reference x (1 + trail).
- A stop hit closes the position and immediately reverses into the opposite side; EMA is not consulted again.
- No fixed take-profit, averaging down, martingale, or doubling.

## Tested trail grid
0.05%, 0.10%, 0.15%, 0.20%, 0.30%, 0.50%, 0.75%, 1.00%.

## Execution model
Configurable entry fee, exit fee and adverse slippage. Funding is zero unless historical funding data is explicitly supplied; it is never invented.

Historical BTCUSDT USD-M Futures aggregate trades are sourced from Binance public data. Binance documents that USD-M Futures aggTrades correspond to the fapi/v1/aggTrades endpoint, with daily/monthly archives and SHA256 checksum files.

This component is research-only until a separate live execution boundary is reviewed and enabled.
