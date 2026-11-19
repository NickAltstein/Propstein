# `feat/price-slip` Blockers & Merge Preconditions

**Status:** DO NOT MERGE.

This branch (`feat/price-slip`) must not be merged into `main` until all seven blockers below are resolved as preconditions:

1. **Hardcoded `rho`**: Correlation coefficients are hardcoded rather than empirically estimated/calibrated from historical settled outcomes with out-of-sample validation.
2. **Metadata-only fixture hash**: Provenance hashing covers fixture/metadata fields rather than raw payload bytes from a verified quote source.
3. **No payout table or EV**: The pricer does not evaluate an explicit slip payout table or expected value (`EV`) against actual book payout multipliers.
4. **Side-unaware `rho`**: Correlation signs do not account for leg direction (`OVER` vs `UNDER`) when combining legs.
5. **Silent matrix repair**: Non-positive-semidefinite correlation matrices are silently repaired instead of failing closed or surfacing an explicit diagnostic.
6. **Request-supplied policy**: The endpoint accepts policy parameters from the caller request instead of enforcing a committed, hash-pinned policy registry.
7. **`ImportError` fallbacks**: Broad `ImportError` fallbacks mask missing runtime dependencies instead of failing closed.
