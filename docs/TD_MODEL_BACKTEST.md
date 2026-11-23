# NFL Anytime Touchdown Model — Walk-Forward Backtest

> **Status:** Research-only (`actionable=False`, `gate_status=RESEARCH_ONLY_UNVERIFIED`).
> Expanding-window walk-forward with strict temporal cutoff (`season < S` or `season == S and week < W`).

## Summary Metrics

- **Evaluated player-games:** `21569` across seasons `[2022, 2023, 2024, 2025]`
- **Observed Anytime TD rate:** `0.2084` vs. **Mean predicted:** `0.2441`
- **Anytime TD Brier:** Model `0.15860` vs. Naive `0.16452` (diff `-0.00592`; negative = better)
- **Anytime TD Log Loss:** Model `0.49426` vs. Naive `0.51042` (diff `-0.01616`; negative = better)
- **2+ TD Brier / Log Loss:** `0.03185` / `0.13723`

## Per-Season Breakdown

| Season | N | Obs Rate | Mean Pred | Model Brier | Naive Brier | Brier Diff | Model LogLoss |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 5380 | 0.2035 | 0.2446 | 0.15751 | 0.16173 | -0.00421 | 0.49247 |
| 2023 | 5422 | 0.2020 | 0.2425 | 0.15503 | 0.16093 | -0.00590 | 0.48631 |
| 2024 | 5357 | 0.2164 | 0.2456 | 0.16175 | 0.16900 | -0.00725 | 0.50128 |
| 2025 | 5410 | 0.2118 | 0.2438 | 0.16014 | 0.16645 | -0.00632 | 0.49707 |

## Reliability Deciles (Anytime TD)

| Bin | N | Mean Pred | Obs Rate | Obs - Pred |
|---:|---:|---:|---:|---:|
| 1 | 2157 | 0.1473 | 0.1122 | -0.0351 |
| 2 | 2157 | 0.1679 | 0.1173 | -0.0506 |
| 3 | 2157 | 0.1825 | 0.1377 | -0.0448 |
| 4 | 2157 | 0.1962 | 0.1637 | -0.0325 |
| 5 | 2157 | 0.2107 | 0.1697 | -0.0410 |
| 6 | 2156 | 0.2271 | 0.1888 | -0.0384 |
| 7 | 2157 | 0.2482 | 0.2202 | -0.0280 |
| 8 | 2157 | 0.2777 | 0.2550 | -0.0227 |
| 9 | 2157 | 0.3246 | 0.3037 | -0.0209 |
| 10 | 2157 | 0.4590 | 0.4159 | -0.0431 |
