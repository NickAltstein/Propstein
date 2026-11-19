# Ladder Integrity Specification (Read-Only Diagnostic)

**Status:** Spec & diagnostic tool committed; report pending >= 1 full week of `data/ladder_captures.jsonl` captures.  
**Gate Isolation:** This diagnostic is **excluded from the CLV gate** (`EDGE_GATE`, `§13`, `§14`) and produces no action fields, no edges, and no stakes.

## 1. Rung Inclusion Criteria
A captured rung enters the integrity check only if it has:
1. Valid `yes_bid` (not `null`),
2. Valid `yes_ask` (not `null`), and
3. Positive top-of-book contract depth on both sides (`yes_bid_depth > 0` and `no_bid_depth > 0`, `status == "OK"`).

Rungs missing any of these (`NO_BOOK`, `ONE_SIDED_YES_ONLY`, `ONE_SIDED_NO_ONLY`, or zero depth) are **excluded and counted** per snapshot.

## 2. Definitions & Placeholder Thresholds (For Nick's Approval)
- **Monotonicity break:** Within a single `snapshot_id`, a higher threshold rung (`k_high > k_low`) has an executable `yes_ask` strictly below a lower rung's executable `yes_bid`:
  `yes_ask(k_high) < yes_bid(k_low)` (with positive depth on both sides).
- **Monotone survival curve fit:** Fit a non-increasing survival curve $S(k) \in [0, 1]$ across executable rungs using Pool Adjacent Violators Algorithm (PAVA) bounded by each rung's executable `[yes_bid, yes_ask]` interval (fitting midpoints clamped/evaluated against executable `[yes_bid, yes_ask]`).
- **Outlier rung:** For each rung $k_i$ (when at least 3 executable rungs exist), fit the monotone survival curve through the *other* rungs $\{k_j : j \ne i\}$, interpolate/bound the expected interval at $k_i$, and measure the minimum distance in cents from rung $i$'s executable `[yes_bid, yes_ask]` interval to that monotone curve. A rung is flagged as an outlier if deviation exceeds **`X` cents**:
  - `OUTLIER_THRESHOLD_CENTS = 5` *(PLACEHOLDER for Nick — do not treat as approved policy)*
- **Minimum depth threshold (diagnostic):**
  - `MIN_RUNG_DEPTH_CONTRACTS = 1` *(PLACEHOLDER for Nick)*
- **Convergence test (T-60 vs Close):** Compare the mean and max outlier deviation across snapshots in the `T-60` window (`45 <= offset_minutes <= 75`) against snapshots in the close window (`0 <= offset_minutes <= 15`) to test whether `deviation_close < deviation_t60`.
