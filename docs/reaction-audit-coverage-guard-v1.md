# Reaction Audit Coverage Guard v1

The market-news reaction audit must distinguish **data coverage** from **market reaction classification**.

A news event is `OUTSIDE_CANDLE_COVERAGE` when its point-in-time event timestamp is later than the final observation in the frozen candle artifact. Such an event is not passed to the reaction or participation classifiers and therefore cannot alter reaction counts.

An event that lies within the frozen sample but lacks one or more bounded reaction horizons remains unclassified with `coverage_status=INSUFFICIENT_REACTION_WINDOW`. This is intentionally different from being outside the artifact's coverage.

The report exposes top-level `market_coverage` and `coverage_counts` so a partial historical sample cannot be mistaken for a complete event study.

These rules are outcome-blind. Trade action, target/stop outcome and P&L are not used to decide coverage or reaction state.
