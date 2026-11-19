# Shadow Dispersion Backtest on `nflverse` (`shadow_v1`)

**Status:** Read-only offline backtest (`2021–2025` `nflverse`). **No production code reads `shadow_v1`; no model swap.**

## 1. Baseline Identity & Data Verification
- **Baseline:** Exact production `ReceivingModel` (`src/model.py`: `ReceivingModel.fit`, `predict_params`, `p_yards_over_vec`) with `cols = ["prior_rec_mean", "prior_ypc_mean", "prior_adot", "prior_ay_share"]`.
- **`shadow_v1`:** Uses the exact same `mu_rec`, `alpha_nb`, and `mu_ypr` from `ReceivingModel`, replacing only pooled `k` with `k(prior_adot, prior_ay_share) = exp(alpha_k + beta_adot * z_adot + beta_ay_share * z_ays)` tuned on `2023` only.
- **Missingness (`WR/TE/RB`):** `receiving_air_yards` has **0 NAs** in all 5 seasons (`2021`: 4,835 rows; `2022`: 4,812; `2023`: 4,847; `2024`: 4,783; `2025`: 5,629). `ngs_air_yards` is absent.

## 2. Split & Common Bucketing
- **Split:** Fit `ReceivingModel` on `2021–2022` (`alpha_nb = 0.121`, `k = 1.5444`) → Tune `shadow_v1` dispersion on `2023` (`alpha_k = 0.1008`, `beta_adot = -0.4785`, `beta_ay_share = 1.5`) → Check on `2024` → Final holdout on `2025` (**touched once**).
- **Common bucket key:** Both models are bucketed by the **baseline probability** (`p_base`) across `[24.5, 39.5, 59.5, 79.5]` yards so every bucket compares identical rows.
- **Multiple-comparison correction:** Holm–Bonferroni step-down across the 7 tests per split (5 bucket log-score tests + 2 overall tests) using 300 player-clustered bootstrap replicates.

## 3. Check Split (`2024`: 4,518 player-games, 431 players)
| Metric / Bucket | `n_obs` | Base | `shadow_v1` | Delta (`shadow - base`) | 95% Clustered CI | Raw `p` | Holm `p` | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Overall Log Score** | 18,072 | 0.381199 | 0.384750 | +0.003550 | `[+0.001826, +0.005007]` | 0.006645 | **0.046512** | **worse** |
| **Overall CRPS** | 4,518 | 12.8304 | 12.7848 | -0.0456 | `[-0.0952, +0.0083]` | 0.086379 | 0.431894 | no evidence of difference |
| `0–20%` | 10,705 | 0.0655 (cal err 0.0029) | 0.0829 (cal err 0.0146) | +0.005887 | `[+0.003302, +0.008479]` | 0.006645 | **0.046512** | **worse** |
| `20–35%` | 3,089 | 0.2695 (cal err 0.0115) | 0.2612 (cal err 0.0198) | -0.000949 | `[-0.002239, +0.000229]` | 0.112957 | 0.451827 | no evidence of difference |
| `35–65%` | 3,004 | 0.4779 (cal err 0.0377) | 0.4846 (cal err 0.0310) | -0.000138 | `[-0.002215, +0.001933]` | 0.843854 | 1.000000 | no evidence of difference |
| `65–80%` | 811 | 0.7171 (cal err 0.0141) | 0.7580 (cal err 0.0268) | +0.003142 | `[-0.005529, +0.010981]` | 0.478405 | 1.000000 | no evidence of difference |
| `80–100%` | 463 | 0.8603 (cal err 0.0122) | 0.8993 (cal err 0.0267) | +0.004186 | `[-0.008753, +0.018894]` | 0.564784 | 1.000000 | no evidence of difference |

## 4. Final Holdout (`2025`, touched once: 5,277 player-games, 485 players)
| Metric / Bucket | `n_obs` | Base | `shadow_v1` | Delta (`shadow - base`) | 95% Clustered CI | Raw `p` | Holm `p` | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Overall Log Score** | 21,108 | 0.335400 | 0.340649 | +0.005249 | `[+0.003578, +0.006912]` | 0.006645 | **0.046512** | **worse** |
| **Overall CRPS** | 5,277 | 11.6780 | 11.4304 | -0.2476 | `[-0.3011, -0.1821]` | 0.006645 | **0.046512** | **improves** |
| `0–20%` | 13,521 | 0.0637 (cal err 0.0097) | 0.0825 (cal err 0.0285) | +0.007922 | `[+0.005288, +0.010033]` | 0.006645 | **0.046512** | **worse** |
| `20–35%` | 3,290 | 0.2654 (cal err 0.0268) | 0.2552 (cal err 0.0166) | -0.003605 | `[-0.004983, -0.002210]` | 0.006645 | **0.046512** | **improves** |
| `35–65%` | 3,057 | 0.4778 (cal err 0.0031) | 0.4822 (cal err 0.0075) | +0.001960 | `[-0.000228, +0.004303]` | 0.093023 | 0.279070 | no evidence of difference |
| `65–80%` | 796 | 0.7188 (cal err 0.0035) | 0.7596 (cal err 0.0372) | +0.005234 | `[-0.001862, +0.013287]` | 0.152824 | 0.279070 | no evidence of difference |
| `80–100%` | 444 | 0.8662 (cal err 0.0036) | 0.9029 (cal err 0.0403) | +0.012137 | `[-0.001893, +0.030989]` | 0.093023 | 0.279070 | no evidence of difference |

## 5. Conclusion
Using the exact `ReceivingModel` baseline and common baseline-probability bucketing, `shadow_v1` **worsens** overall log score and the `0–20%` lower tail on both `2024` and `2025` (`holm_p = 0.046512`), shows **no evidence of difference** in `35–100%`, and improves only `20–35%` and CRPS on `2025`. Keep the registered production model frozen; **do not swap**.
