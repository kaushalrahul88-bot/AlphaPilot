# Crude Oil Domain Knowledge V1

AlphaPilot's Crude Oil Mini Market Brain should know how the commodity works without confusing permanent domain knowledge with historical event/news data.

## Governance

- Domain knowledge is research-only.
- It supplies mechanisms, conditional priors, exceptions and experiment hooks.
- It cannot create BUY_CE, BUY_PE, WAIT changes or production orders by itself.
- Historical events/news remain separate and require point-in-time `first_detected_at` visibility.
- Scheduled data such as EIA releases require a point-in-time expectation before a surprise can be calculated.
- Missing context remains unknown.
- Copper thresholds, fitted values and outcomes are not transferred to Crude Oil Mini.
- Regular CRUDEOIL is not a substitute for the CRUDEOILM market tape.

## Knowledge families

The first pack covers:

1. MCX Crude Oil Mini relationship to global WTI.
2. USD/INR translation as a Crude-specific research hypothesis.
3. Global physical supply/demand balance.
4. EIA crude inventories.
5. Gasoline/distillate confirmation.
6. Refinery utilization and crude inputs.
7. OPEC/OPEC+ supply management.
8. Non-OPEC supply.
9. Geopolitical physical-flow risk.
10. Weather disruption channels.
11. Futures term structure: contango/backwardation.
12. Chinese/global demand context.
13. Separation of underlying direction from option implied-volatility/premium economics.

The pack uses authoritative MCX, EIA and OPEC sources wherever possible, with CME research used for futures-curve context.

## Intended research sequence

`domain knowledge -> identify observable context -> enforce PIT availability -> context coverage audit -> ablation against local-tape baseline -> candidate hypothesis -> chronological/holdout validation -> only then allow a validated context feature into the Market Brain`

News Intelligence remains a later, separate same-click experiment after the no-news Brain is frozen.
