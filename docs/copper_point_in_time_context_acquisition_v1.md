# Copper Point-in-Time External Context Acquisition v1

## Purpose
Acquire only historical information that could genuinely have been visible at a simulated AlphaPilot click. Daily summaries are not silently promoted to intraday observations.

## Source policy
- MCX Copper: existing stored COPPER31AUG26FUT 5-minute candles are the primary replay clock.
- COMEX HG: CME DataMine / licensed CME historical market data is authoritative. DataMine API access requires authentication and entitlement to purchased files. Until entitled intraday data is present, COMEX_HG remains unavailable to click-time replay.
- LME Copper: use LME/licensed historical data only when timestamp granularity and availability time can be established.
- USDINR: intraday source required for click-time features. RBI/Federal Reserve daily/reference observations may be retained as daily macro context, but never masquerade as a 5-minute FX quote.
- DXY: intraday source required for click-time features. Federal Reserve H.10 dollar indexes are daily observations and may be used only at their genuine publication availability.
- USDCNY/CNH: same point-in-time rule; intraday data required for click-time market context.
- News: require publication timestamp and source. Revisions/updates must not overwrite what the simulated user could have known.
- Macro: store release timestamp, actual, prior, consensus when genuinely available, and revision metadata.
- MCX options: collect forward from AlphaPilot's own option collectors unless a legitimate historical MCX option source is proven.

## Required record fields
series, observed_at, available_at, source, value, quality

## Hard guard
A record is visible only when both observed_at <= click_timestamp and available_at <= click_timestamp.

## Prohibited shortcuts
- no using end-of-day COMEX/LME/FX values at an earlier intraday click;
- no using article/event timestamps inferred from later retrospective reporting as if they were live headlines;
- no interpolating missing external data from MCX's later price path;
- no fabricating historical option premium/IV/Greeks;
- no tuning source availability rules after seeing replay outcomes.
