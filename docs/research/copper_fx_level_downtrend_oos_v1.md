# Copper FX Level × Downtrend OOS v1 — Preregistration

Status: **FROZEN BEFORE OOS EVALUATION**

## Motivation
The descriptive Copper Context Interaction Audit v1 (run 33252008017) contained 1,055 observations. Among non-sparse structure interactions, the strongest interpretable positive cell was LOW USD/INR expanding-percentile × DOWNTREND: 46 signals, 52.17% win rate, +0.0522% average net return after the audit's 4 bps round-trip cost, profit factor 1.439.

This document freezes the hypothesis before any chronological OOS evaluation. The descriptive result is discovery evidence only and is not a production rule.

## Frozen hypothesis
For existing Brain-A SELL observations whose frozen market structure is DOWNTREND, a LOW point-in-time USD/INR level bucket (expanding percentile <= 0.25) is hypothesized to improve 60-minute net expectancy relative to the unfiltered DOWNTREND SELL baseline.

## Frozen feature and timing
- Context: USD/INR level only.
- Bucket: LOW iff the existing expanding percentile is <= 0.25.
- Availability: existing point-in-time context policy; no future context may be used.
- Market structure: existing DOWNTREND definition; unchanged.
- Horizon: 60 minutes.
- Round-trip cost: 4.0 bps.
- No threshold, horizon, cost, structure definition, or context timing may be changed after this preregistration based on OOS results.

## Chronological evaluation
Split eligible observations strictly by timestamp:
- discovery/train: earliest 70%
- untouched OOS: latest 30%

The OOS section is not inspected to redefine the hypothesis.

Compare on OOS:
1. all existing Brain-A DOWNTREND SELL observations;
2. the subset also satisfying LOW USD/INR level.

## Frozen promotion gate
The hypothesis passes only if the OOS subset:
- has at least 20 signals;
- has average net return > 0 after 4 bps round-trip cost;
- has profit factor > 1.10;
- improves average net return versus the contemporaneous OOS DOWNTREND SELL baseline;
- does not rely on UNKNOWN context.

Failure of any gate means **NO PROMOTION**. No fallback interaction is substituted from the same audit.

## Scope
Research only. Passing this OOS gate still does not change Market Brain or create an option trade rule. A passing underlying-context hypothesis must subsequently undergo option-translation validation before any production consideration.


## Methodology correction before evaluation
The descriptive interaction audit inspected the full stored sample used to select this candidate. Therefore the originally proposed latest-30% split cannot honestly be called untouched OOS after candidate selection.

This correction is made **before evaluating the candidate on that split** and does not change the frozen hypothesis, feature threshold, horizon, cost, or promotion metrics.

The evaluation policy is therefore:
- the 70/30 chronological split is retained as a **diagnostic temporal stress test only**;
- it is permanently **ineligible for promotion**;
- true promotion eligibility begins only with observations whose market timestamp is strictly after the completed discovery audit cutoff: **2026-08-29T12:16:54Z**;
- the original frozen promotion gates apply to that genuinely forward sample;
- until at least 20 qualifying LOW-USDINR × DOWNTREND SELL observations exist after the cutoff, status is **WAITING_FOR_FORWARD_SAMPLE**.

This supersedes only the claim that the latest 30% of the already-inspected dataset is untouched OOS. It does not permit selecting a fallback interaction from the discovery audit.
