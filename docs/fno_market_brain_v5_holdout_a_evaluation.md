# F&O Market Brain V5 — Historical Holdout A Evaluation

Evaluation protocol: `FNO_MARKET_BRAIN_V5_HOLDOUT_A_EVALUATION_V1_2026-09-08`

Source dataset: `FNO_MARKET_BRAIN_V5_HOLDOUT_A_2026-09-07`  
Frozen Brain: `a5a39253c1aa79f6663cd11b41f7d6607b96af90`  
Primary horizon: **90 minutes**  
Mode: **historical holdout only; not forward testing**

## Integrity

- Source observations: **1600**
- Effective unique actionable theses: **63** (38 LONG, 25 SHORT)
- Duplicate thesis suppressions: **92**
- All frozen 5m tape hashes matched: **True**
- Outcomes were unresolved and evaluation metrics absent in the source dataset before scoring.

## Primary 90m result

- Correct: **32**
- Incorrect: **31**
- Flat: **0**
- Non-flat accuracy: **50.7937%**
- 95% Wilson interval: **38.7629% – 62.7332%**
- Exact one-sided binomial p vs 50%: **0.50000000**
- Mean directional return: **+0.056134%**
- Median directional return: **+0.011108%**
- Mean MFE: **+0.474169%**
- Mean MAE: **-0.412910%**

## Frozen gate result

| Gate | Result | Key observation |
|---|---|---|
| Sample sufficiency | **FAIL** | 63 unique theses vs required >=80 |
| Directional accuracy | **FAIL** | 50.7937% vs required >=58%; p=0.5 |
| Directional payoff | **PASS** | mean +0.056134%, median +0.011108% |
| Temporal robustness | **FAIL** | July accuracy 48.7179% |
| Cross-stock robustness | **FAIL** | only INFY and BHARTIARTL positive mean; HDFCBANK/MARUTI below 45% accuracy |
| Concentration control | **PASS** | max stock 31.7460%; max session 11.1111% |

**Holdout A overall: FAIL.**

## 90m diagnostics

### By window

| Window | N | Accuracy | Mean dir. return |
|---|---:|---:|---:|
| June 2026 | 24 | 54.1667% | +0.059103% |
| July 2026 | 39 | 48.7179% | +0.054307% |

### By stock

| Stock | N | Accuracy | Mean dir. return |
|---|---:|---:|---:|
| HDFCBANK | 20 | 40.0000% | -0.153900% |
| INFY | 14 | 71.4286% | +0.443530% |
| MARUTI | 13 | 15.3846% | -0.310925% |
| BHARTIARTL | 16 | 75.0000% | +0.277941% |

### By side

| Side | N | Accuracy | Mean dir. return |
|---|---:|---:|---:|
| LONG | 38 | 55.2632% | +0.182611% |
| SHORT | 25 | 44.0000% | -0.136111% |

### By setup type

All **63** effective actionable theses were `TRANSITION`. `PULLBACK_REENTRY` produced no actionable thesis in Holdout A.

## Secondary horizons

| Horizon | Accuracy | Mean dir. return | Median dir. return |
|---|---:|---:|---:|
| 15m | 47.5410% | +0.008029% | -0.006759% |
| 30m | 48.3871% | +0.005471% | -0.028681% |
| 60m | 48.3871% | +0.043775% | -0.006589% |
| 90m | 50.7937% | +0.056134% | +0.011108% |
| EOD | 46.0317% | +0.008885% | -0.099004% |

## Interpretation boundary

Because Holdout A failed, these outcomes now become **development evidence**. V5 cannot proceed to Holdout B or forward testing as a promoted strategy. Any outcome-driven change must be a new Brain version (V6 or later), and this Holdout A cannot be reused as that revised version's promotion holdout.
