# NFL Anytime Touchdown Model — Walk-Forward Backtest

> **Status:** Research-only (`actionable=False`, `gate_status=RESEARCH_ONLY_UNVERIFIED`).
> Expanding-window walk-forward with strict temporal cutoff (`season < S` or `season == S and week < W`).

## Summary Metrics

- **Evaluated player-games:** `21569` across seasons `[2022, 2023, 2024, 2025]`
- **Observed Anytime TD rate:** `0.2084` vs. **Mean predicted:** `0.2072`
- **Anytime TD Brier:** Model `0.15753` vs. Naive `0.16452` (diff `-0.00699`; negative = better)
- **Anytime TD Log Loss:** Model `0.49051` vs. Naive `0.51042` (diff `-0.01992`; negative = better)
- **2+ TD Brier / Log Loss:** `0.03143` / `0.13535`

## Per-Season Breakdown

| Season | N | Obs Rate | Mean Pred | Model Brier | Naive Brier | Brier Diff | Model LogLoss |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 5380 | 0.2035 | 0.2099 | 0.15597 | 0.16173 | -0.00576 | 0.48770 |
| 2023 | 5422 | 0.2020 | 0.2043 | 0.15367 | 0.16093 | -0.00725 | 0.48151 |
| 2024 | 5357 | 0.2164 | 0.2067 | 0.16121 | 0.16900 | -0.00779 | 0.49893 |
| 2025 | 5410 | 0.2118 | 0.2078 | 0.15930 | 0.16645 | -0.00715 | 0.49397 |

## Reliability Deciles (Anytime TD)

| Bin | N | Mean Pred | Obs Rate | Obs - Pred |
|---:|---:|---:|---:|---:|
| 1 | 2157 | 0.1228 | 0.1099 | -0.0129 |
| 2 | 2158 | 0.1403 | 0.1214 | -0.0189 |
| 3 | 2157 | 0.1528 | 0.1331 | -0.0198 |
| 4 | 2156 | 0.1645 | 0.1684 | +0.0038 |
| 5 | 2157 | 0.1771 | 0.1715 | -0.0056 |
| 6 | 2156 | 0.1914 | 0.1851 | -0.0063 |
| 7 | 2157 | 0.2097 | 0.2188 | +0.0092 |
| 8 | 2157 | 0.2356 | 0.2554 | +0.0199 |
| 9 | 2157 | 0.2771 | 0.3060 | +0.0289 |
| 10 | 2157 | 0.4005 | 0.4145 | +0.0140 |
