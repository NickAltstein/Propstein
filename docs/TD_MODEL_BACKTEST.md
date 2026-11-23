# NFL Anytime Touchdown Model — Walk-Forward Backtest

> **Status:** Research-only (`actionable=False`, `gate_status=RESEARCH_ONLY_UNVERIFIED`).
> Expanding-window walk-forward with strict temporal cutoff (`season < S` or `season == S and week < W`).

## Summary Metrics

- **Evaluated player-games:** `21569` across seasons `[2022, 2023, 2024, 2025]`
- **Observed Anytime TD rate:** `0.2084` vs. **Mean predicted:** `0.2071`
- **Anytime TD Brier:** Model `0.15753` vs. Naive `0.16452` (diff `-0.00699`; negative = better)
- **Anytime TD Log Loss:** Model `0.49050` vs. Naive `0.51042` (diff `-0.01992`; negative = better)
- **2+ TD Brier / Log Loss:** `0.03143` / `0.13535`

## Per-Season Breakdown

| Season | N | Obs Rate | Mean Pred | Model Brier | Naive Brier | Brier Diff | Model LogLoss |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 5380 | 0.2035 | 0.2097 | 0.15597 | 0.16173 | -0.00576 | 0.48769 |
| 2023 | 5422 | 0.2020 | 0.2043 | 0.15367 | 0.16093 | -0.00725 | 0.48151 |
| 2024 | 5357 | 0.2164 | 0.2067 | 0.16121 | 0.16900 | -0.00779 | 0.49893 |
| 2025 | 5410 | 0.2118 | 0.2077 | 0.15930 | 0.16645 | -0.00715 | 0.49397 |

## Reliability Deciles (Anytime TD)

| Bin | N | Mean Pred | Obs Rate | Obs - Pred |
|---:|---:|---:|---:|---:|
| 1 | 2157 | 0.1227 | 0.1103 | -0.0124 |
| 2 | 2157 | 0.1402 | 0.1215 | -0.0188 |
| 3 | 2157 | 0.1528 | 0.1331 | -0.0197 |
| 4 | 2157 | 0.1645 | 0.1683 | +0.0038 |
| 5 | 2157 | 0.1770 | 0.1701 | -0.0069 |
| 6 | 2157 | 0.1913 | 0.1864 | -0.0049 |
| 7 | 2156 | 0.2096 | 0.2194 | +0.0098 |
| 8 | 2157 | 0.2355 | 0.2536 | +0.0181 |
| 9 | 2157 | 0.2770 | 0.3069 | +0.0299 |
| 10 | 2157 | 0.4004 | 0.4145 | +0.0141 |
