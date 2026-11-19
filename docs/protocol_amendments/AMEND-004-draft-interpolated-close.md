# AMEND-004: Model-Free Interpolated Close (`CLOSE_INTERPOLATED`)

- **Amendment ID:** AMEND-004
- **Status:** `DRAFT`
- **Effective UTC Timestamp:** *(blank — pending Nick's approval)*
- **Gate Isolation:** Shadow-only comparison helper; not called by any gate, grading path, or `/v1/edges` endpoint. Does not alter `EDGE_GATE`, `§13`, `§14`, or existing ledger rows.

## 1. Definition of `CLOSE_INTERPOLATED`
When a bet line falls strictly between (or on) two adjacent closing ladder rungs (`low.rung_threshold <= bet_line <= high.rung_threshold` with `low.rung_threshold < high.rung_threshold`), `CLOSE_INTERPOLATED` computes the closing implied probability by **monotone linear interpolation in probability space**:

$$p(\text{bet\_line}) = p_{\text{low}} + \frac{\text{bet\_line} - k_{\text{low}}}{k_{\text{high}} - k_{\text{low}}} \cdot (p_{\text{high}} - p_{\text{low}})$$

where $p_{\text{low}}$ and $p_{\text{high}}$ are the midpoint probabilities $\frac{1}{2}(\text{yes\_bid} + \text{yes\_ask})$ of the two bracketing rungs. Every `CLOSE_INTERPOLATED` output stores:
- Both bracketing rungs (`low_rung`, `high_rung`),
- Their executable `yes_bid`, `yes_ask`, `yes_bid_depth`, and `no_bid_depth`,
- `method_name = "monotone_probability_linear_bracket_v1"`, and
- Optional side-by-side `close_translated_prob` for shadow comparison against `CLOSE_TRANSLATED`.

## 2. Guard Rails (Committed Values Are Placeholders for Nick)
All placeholder values are defined in `src/contracts/ladder_placeholders.py` and **are to be set from spacing and depth distributions after week one, committed before any convergence analysis**. If any guard rail fails, the result is `CLOSE_UNEVALUABLE`:
1. **Two-sided bracket required:** Must have both `low.rung_threshold <= bet_line` and `high.rung_threshold >= bet_line` with `low < high`.
2. **Maximum rung gap (relative to observed spacing per stat):** `k_high - k_low <= MAX_RUNG_GAP_MULTIPLIER * observed_median_spacing_per_stat` (`2.0x` — *PLACEHOLDER for Nick*; e.g., `5.0` for NBA points, `10.0` for NFL receiving yards, `1.0` for NFL receptions).
3. **Monotonicity check:** `p_low >= p_high` and no inversion (`yes_bid(high) <= yes_ask(low)`).
4. **Executable book & minimum depth (shared with ladder integrity spec):** Both bracketing rungs must have `status == "OK"` (not `NO_BOOK` or one-sided) and `yes_bid_depth >= MIN_RUNG_DEPTH_CONTRACTS` and `no_bid_depth >= MIN_RUNG_DEPTH_CONTRACTS` (`PLACEHOLDER_MIN_DEPTH_CONTRACTS` in `src/contracts/ladder_placeholders.py`).
5. **Same `snapshot_id` & close window:** Both bracketing rungs must share the exact same `snapshot_id` captured within the close window (`0 <= offset_minutes <= CLOSE_WINDOW_MINUTES`, `15.0` min — *PLACEHOLDER for Nick*).
