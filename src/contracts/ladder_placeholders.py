"""Shared placeholder thresholds for ladder integrity (P11-B) and interpolated close (P11-C).

Values are placeholders for Nick and must be set from week-one spacing and depth
distributions and committed before any convergence analysis.
"""

from __future__ import annotations

PLACEHOLDER_MIN_DEPTH_CONTRACTS = 1.0
PLACEHOLDER_OUTLIER_THRESHOLD_CENTS = 5.0
PLACEHOLDER_MAX_RUNG_GAP_MULTIPLIER = 2.0  # max_rung_gap = multiplier * observed_median_spacing_per_stat
PLACEHOLDER_CLOSE_WINDOW_MINUTES = 15.0
