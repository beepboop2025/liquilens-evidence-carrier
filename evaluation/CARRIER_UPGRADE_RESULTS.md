# Carrier paper-policy candidate comparison

Candidate `bf589f7166788a813744ee9e5247db84aa7e2ba9` was evaluated against the unchanged installed-release baseline on the same 79,205 hourly observations and all 21 predeclared scenarios. These are BTCUSDT price-proxy construction diagnostics, with assumed USDT/USD parity and historical close availability; they do not validate native BTC/USD or a complete historical copilot chain.

The candidate reserves one of the existing two daily attempts for reductions and removes only the rebalance-tolerance obstruction to valid $1,000 negative-momentum sells. It retains minimum size, daily loss, pending-order, evidence, no-short and broker gates. Positive drift is diagnosed without forcing a sale. No thresholds were chosen to maximize these returns.

At 5 bp and one-bar lag, CALM cumulative return falls from 38.34% to 22.61%, alongside lower drawdown. At 50 bp and one-bar lag, CALM changes from +8.33% to -0.18%; EROSION remains negative at -6.33% (baseline -7.58%). The candidate is not a universal performance improvement; these adverse outcomes remain part of the fixed grid.

## Fixed-grid performance

Returns are cumulative over the full available interval (partial 2017 and 2026), not annualized. Drawdown uses close marks; terminal positions are not forcibly liquidated. Costs are the prescribed all-in charge per filled side.

| Regime | Cost bp | Lag bars | Baseline return | Candidate return | Baseline DD | Candidate DD | Baseline fills | Candidate fills | Baseline costs | Candidate costs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CALM | 1 | 1 | 42.21% | 24.38% | 7.49% | 2.81% | 5,979 | 4,535 | $597.90 | $453.50 |
| CALM | 1 | 2 | 38.79% | 23.84% | 7.57% | 3.43% | 5,953 | 4,539 | $595.30 | $453.90 |
| CALM | 5 | 1 | 38.34% | 22.61% | 7.40% | 3.04% | 5,980 | 4,533 | $2,990.00 | $2,266.50 |
| CALM | 5 | 2 | 36.37% | 22.03% | 7.84% | 3.57% | 5,942 | 4,539 | $2,971.00 | $2,269.50 |
| CALM | 10 | 1 | 35.20% | 20.35% | 7.75% | 3.33% | 5,970 | 4,533 | $5,970.00 | $4,533.00 |
| CALM | 10 | 2 | 33.17% | 19.76% | 8.18% | 3.75% | 5,940 | 4,539 | $5,940.00 | $4,539.00 |
| CALM | 25 | 1 | 23.83% | 13.52% | 8.73% | 4.39% | 5,956 | 4,523 | $14,890.00 | $11,307.50 |
| CALM | 25 | 2 | 23.93% | 12.95% | 8.93% | 4.86% | 5,926 | 4,537 | $14,815.00 | $11,342.50 |
| CALM | 50 | 1 | 8.33% | -0.18% | 9.91% | 9.90% | 5,931 | 4,533 | $29,655.00 | $22,665.00 |
| CALM | 50 | 2 | 7.30% | 1.50% | 10.73% | 10.03% | 5,898 | 4,533 | $29,490.00 | $22,665.00 |
| EROSION | 1 | 1 | 18.73% | 15.11% | 4.19% | 2.53% | 5,371 | 4,370 | $537.10 | $437.00 |
| EROSION | 1 | 2 | 17.79% | 13.87% | 3.69% | 2.48% | 5,310 | 4,375 | $531.00 | $437.50 |
| EROSION | 5 | 1 | 16.32% | 13.74% | 4.43% | 2.66% | 5,355 | 4,370 | $2,677.50 | $2,185.00 |
| EROSION | 5 | 2 | 14.72% | 13.28% | 4.83% | 2.77% | 5,303 | 4,368 | $2,651.50 | $2,184.00 |
| EROSION | 10 | 1 | 15.24% | 11.56% | 4.60% | 2.91% | 5,327 | 4,368 | $5,327.00 | $4,368.00 |
| EROSION | 10 | 2 | 12.13% | 10.95% | 4.58% | 3.07% | 5,301 | 4,364 | $5,301.00 | $4,364.00 |
| EROSION | 25 | 1 | 6.73% | 4.62% | 5.68% | 3.71% | 5,300 | 4,350 | $13,250.00 | $10,875.00 |
| EROSION | 25 | 2 | 4.86% | 4.34% | 5.50% | 4.10% | 5,276 | 4,348 | $13,190.00 | $10,870.00 |
| EROSION | 50 | 1 | -7.58% | -6.33% | 14.37% | 10.96% | 5,192 | 4,322 | $25,960.00 | $21,610.00 |
| EROSION | 50 | 2 | -9.16% | -6.75% | 15.76% | 11.78% | 5,144 | 4,313 | $25,720.00 | $21,565.00 |
| Missing funding HOLD | 5 | 1 | 0.00% | 0.00% | 0.00% | 0.00% | 0 | 0 | $0.00 | $0.00 |

## Reproduced behavior at 5 bp, lag one

| Metric | CALM baseline → candidate | EROSION baseline → candidate |
| --- | ---: | ---: |
| Negative-signal holdings ≥$1,000 held by tolerance | 1,124 → 0 | 1,268 → 0 |
| Sell proposals held by total daily limit | 20,215 → 8,601 | 12,682 → 7,086 |
| Closes above fixed regime target | 1,393 → 35 | 1,844 → 471 |
| Maximum marked BTC exposure | 13.50% → 11.40% | 6.38% → 5.98% |
| Entries held to preserve exit capacity | 0 → 23,595 | 0 → 20,277 |

Counts refer to hourly observations/proposals, not distinct incidents or completed round trips. Entry reservation changes the whole subsequent holding path, so these differences cannot isolate a causal return contribution from each code change.

Residuals below the fixed minimum still hold explicitly: 16,835 CALM and 18,436 EROSION observations. The target remains an entry target; marked exposure may drift above it. Two total attempts remain a hard ceiling, so reserving exit capacity does not guarantee that every later sell proposal can execute.

## Saved-artifact verification and limits

The independent checker streamed 1,663,305 candidate records and 88,892 fills with zero checked violations. It reconciled equity, costs, fills, drawdown, daily reservations (at most two total and one entry), nonnegative cash/quantity, as-of window size and next-open execution timing. It did not impose a continuously maintained exposure cap. A synthetic 735-row checker test detected deliberately corrupted entry counters and as-of bounds.

The missing-funding refusal lane must keep its distinction: the actual strategy receives no funding regime, and a separate conservative enforcement lane remains HOLD. No contemporaneous full funding/exit/authorization chain was supplied for the historical interval. Price performance cannot authorize execution. Durable database migration, concurrent admission, UTC rollover and pre-broker persistence failures are exercised separately by the 355-test candidate suite (including five adapter tests and 53 subtests).

Sources: `replay/summary.json`, `replay/baseline-comparison.json`, `replay/empirical-review.json`, `replay/harness-adaptation.diff`, `tests/validation.json`, the exact candidate `carrier-source.tar.gz`, and the immutable baseline summary/audit referenced by their hashes. Raw market data remains in the original evaluation run; the candidate uses its verified SHA256 rather than duplicating or modifying it.

Frozen baseline strategy revision: `04f677c6be8143b6268c5ad0446cd91972929650`. Baseline summary SHA256: `909a4537118bea601a7c127a62265e43f0fdbe90cda86346c913f462f836c6ce`. Matched input SHA256: `cc1339f820748efdafdd93fa76f19fccd4918fbea8022538d305d47aab4ac142`. Candidate strategy-file SHA256: `c96367fcc5afdac618a4c0dabe40c18722731ea0c51d94b43d39fe2782b45601`. The full source archive SHA256 is `0aace9477a635329096f3b5193d46a13914b8c144aeea3b4a46d3d0241d90b16`.

Server artifacts: `/mnt/HC_Volume_106588294/liquilens/copilot-upgrades/20260907T071512Z/carrier/`. Baseline remains `/mnt/HC_Volume_106588294/liquilens/copilot-evaluations/20260907T050000Z/`.

This candidate has not been deployed or enabled. Historical performance remains descriptive; same-data comparisons are not held-out validation or evidence of future profitability.
