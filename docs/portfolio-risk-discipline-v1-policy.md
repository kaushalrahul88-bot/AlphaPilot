# Portfolio Risk & Discipline Engine v1 — frozen policy

Protocol revision: `portfolio-risk-discipline-v1-2026-08-25`

## Scope

This is a deterministic paper/control-validation layer. It does not change scanner ranking, entries, stops, exits, strategy parameters, or Market Brain behavior. It cannot place a broker order. LLM output may explain the result but cannot change it.

## Hard decision order

1. Validate BUY CE/PE premium geometry: stop below entry and target above entry.
2. Require explicitly verified account/portfolio state, executable NSE session, fresh intraday candles, complete 44/44 universe scan, all expected F&O confirmations, complete 9/9 quality checks, and adequate liquidity.
3. Enforce daily/weekly realized-loss locks, maximum drawdown, and consecutive-loss cooldown.
4. Enforce maximum concurrent positions, total open risk, correlated-group risk, per-position value, and gross exposure.
5. Calculate the maximum whole-lot quantity from the smallest remaining risk/exposure budget. Costs count toward potential loss.
6. Require net R:R of at least 1.5:1. `NO_TRADE` is a valid and preferred result when any gate fails.

## v1 defaults and immutable caps

| Limit | Default | v1 upper bound |
|---|---:|---:|
| Risk per trade | 1% | 1% |
| Daily realized loss | 3% | 3% |
| Weekly realized loss | 6% | 6% |
| Total open risk | 3% | 6% |
| Correlated-group open risk | 1% | 2% |
| Single position value | 20% | 30% |
| Gross exposure | 50% | 100% |
| Concurrent positions | 2 | 5 |
| Consecutive losses | 3 | 5 |
| Loss cooldown | 60 minutes | 375 minutes |
| Minimum net R:R | 1.5:1 | cannot be lowered |
| Closed-trade drawdown | 8% | 10% |

## Controlled-live preview

A preview can only become evidence-eligible after at least 30 paper trades, 10 clean paper sessions, positive expectancy, profit factor at least 1.20, paper drawdown no worse than 6R, and recorded manual approval. Even then, v1 returns `NO_TRADE` plus `LIVE_EXECUTION_DISABLED_V1`. A later separately reviewed release must add any live capability.
