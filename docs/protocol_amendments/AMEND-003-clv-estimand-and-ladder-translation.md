# AMEND-003: CLV Estimand, Ladder Translation, and Policy Spec Hash

- **Amendment ID:** AMEND-003
- **Effective UTC Timestamp:** `2026-12-01T00:00:00Z`
- **Status:** Defines the estimand for new observations captured at or after the effective timestamp only. No retroactive reinterpretation; `docs/EDGE_GATE.md` thresholds, §1, §13, and unfilled §14 slots are unchanged.

## 1. Historical Immutability & Superseding Corrections
Historical rows recorded with `reason_code = 'LINE_CHANGED'` and any observations captured before `2026-12-01T00:00:00Z` remain untouched under pre-amendment rules. The `closing_references_protect_legacy_update` trigger keeps `close_state` and `reason_code` immutable in place; any correction to a recorded close observation must be written as a new superseding row rather than mutating an existing row.

## 2. Close States & Close Quality (Post-Amendment)
- `CLOSE_EXACT`: Closing reference matches entry line (`round(line, 4)`) with valid two-sided closing odds and passes close quality checks.
- `CLOSE_TRANSLATED`: Closing line differs from entry line, and the closing market provides two bracketing quoted ladder rungs (`low_rung.line <= entry_line <= high_rung.line`, `low_rung.line < high_rung.line`). Translation interpolates linearly between the closing de-vigged probabilities of the two neighboring rungs—never a model distribution and never a half-yard heuristic without a neighboring rung. Every `CLOSE_TRANSLATED` output and receipt is labeled `derived: true` with `translation_method_hash`, and any aggregate over translated closes reports a confidence interval.
- `CLOSE_UNEVALUABLE`: Line changed with only a single closing number and no neighboring bracketing rung, missing/incomplete closing odds, wide closing spread (`CLOSE_WIDE_SPREAD`), thin top-of-book depth (`CLOSE_THIN_DEPTH`), or unapproved placeholder close-quality config (`UNAPPROVED_CLOSE_QUALITY_CONFIG`). The values in `src/contracts/close_quality_config.json` (`2026.11.18-1`: `0.06` / `$250.0`) are unapproved agent placeholders (`grades_nothing_until_section_14_approved: true`) that grade nothing until Nick approves the §14 slots in PR #185.

## 3. Canonical Closing Snapshot v2
Translated closes are hashed under schema `propwire.closing_snapshot.v2` using canonical JSON SHA-256 (`src/crypto_utils.py`). Legacy `propwire.closing_snapshot.v1` receipts remain valid and unchanged.

## 4. Price CLV Decomposition & Shadow Ledger
- Evaluable price CLV decomposes into `line_component`, `juice_component`, and `book_execution_component`.
- Same line with worse juice scores strictly negative.
- Confirmatory sample (Polymarket vs Polymarket): `book_execution_component = 0.0` identically.
- Shadow ledger (non-confirmatory licensed books): computes `book_execution_component` within a single `quote_source` class against the best tape price at the same `source_ts`. Shadow ledger rows cannot unfreeze paid access or affect confirmatory `N`.

## 5. Qualification Policy in Slate Payload
Slate payloads embed `qualification_policy` with `policy_spec_hash = sha256(canonical_json(params))`. Missing policy or hash mismatch forces all action fields off.
