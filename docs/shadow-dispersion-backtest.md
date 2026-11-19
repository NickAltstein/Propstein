# Shadow Dispersion Backtest on `nflverse` (`shadow_v1`)

**Status:** Read-only offline backtest (`2021–2024` `nflverse` `player_stats`). **No production code reads `shadow_v1`; no model swap.**

## 1. Data & Missing-Value Verification (`WR`, `TE`, `RB`)
`ngs_air_yards` is absent from `player_stats` (and `ngs_receiving` omits non-qualified receivers), so only `player_stats.receiving_air_yards` and `pbp.air_yards` were audited:

| Season | `player_stats` rows (`WR/TE/RB`) | `receiving_air_yards` NA | `ngs_air_yards` | `pbp` pass plays | `pbp.air_yards` NA |
|---|---|---|---|---|---|
| 2021 | 4,835 | 0 | ABSENT | 21,012 | 1,431 |
| 2022 | 4,812 | 0 | ABSENT | 20,395 | 1,453 |
| 2023 | 4,847 | 0 | ABSENT | 20,728 | 1,544 |
| 2024 | 4,783 | 0 | ABSENT | 20,007 | 1,502 |

All features below use `player_stats` (`receiving_air_yards`, `targets`, `receptions`, `receiving_yards`).

## 2. Point-in-Time Features & Train/Test Split
- **Rolling window:** Per-player games sorted by `(season, week)` with `.shift(1).rolling(8, min_periods=1)` so game $t$ only sees games strictly before $t$. Team air yards are likewise shifted by 1 week per team (`team_ay_rolling`). Requires `prior_games >= 3`.
- **Train split (frozen before evaluation):** `2021–2022` (`r_rec = 7.1105`, `k_baseline = 8.1431`; `shadow_v1`: `alpha = 1.3882`, `beta_adot = 1.1475`, `beta_ay_share = -1.3554`).
- **Out-of-sample test split (zero tuning):** `2023–2024` (**9,078** player-games, **526** unique players), evaluated across `[24.5, 39.5, 59.5, 79.5]` yards.

## 3. Head-to-Head Out-of-Sample Results (`2023–2024`)
| Metric | Baseline (pooled) | `shadow_v1` | Delta (`shadow - base`) | 95% Player-Clustered CI |
|---|---|---|---|---|
| **Log Score** (lower is better) | 0.404030 | 0.401763 | **-0.002266** | `[-0.003818, -0.000811]` |
| **CRPS** (lower is better) | 12.9486 | 12.9699 | +0.0213 | `[-0.0118, +0.0578]` |

### Calibration by Probability Bucket (`n` across 4 lines = 36,312 evaluations)
| Bucket | `n` (Base / Shadow) | Base Pred / Actual | Shadow Pred / Actual | Base Cal Err | Shadow Cal Err | Delta Log Score | 95% Clustered CI |
|---|---|---|---|---|---|---|---|
| `0-20%` | 22,173 / 21,909 | 0.0440 / 0.0724 | 0.0455 / 0.0710 | 0.0284 | **0.0255** | **-0.004945** | `[-0.006500, -0.003541]` |
| `20-35%` | 4,315 / 4,638 | 0.2719 / 0.2630 | 0.2730 / 0.2602 | **0.0089** | 0.0127 | -0.000538 | `[-0.001178, +0.000163]` |
| `35-65%` | 5,943 / 6,742 | 0.4891 / 0.4437 | 0.4895 / 0.4718 | 0.0454 | **0.0177** | -0.001232 | `[-0.002625, +0.000141]` |
| `65-80%` | 2,259 / 2,367 | 0.7227 / 0.6667 | 0.7192 / 0.7309 | 0.0560 | **0.0117** | +0.001706 | `[-0.005285, +0.008796]` |
| `80-100%` | 1,622 / 656 | 0.8693 / 0.8409 | 0.8401 / 0.8765 | **0.0284** | 0.0364 | **+0.020438** | `[+0.007964, +0.033107]` |

## 4. Tail-Bucket Verdict
- **Lower tail (`0-20%`):** Improved significantly (`-0.004945` log score, calibration error `0.0284 -> 0.0255`).
- **Upper tail (`80-100%`):** Degraded significantly (`+0.020438` log score, calibration error `0.0284 -> 0.0364`, sample shrunk from 1,622 to 656).
- **Conclusion:** Results are mixed (better overall log score and `0-20%` tail, worse `80-100%` tail and neutral/slightly worse CRPS). Keep the registered production model frozen; **do not swap**.
