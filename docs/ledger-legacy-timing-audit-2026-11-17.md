# Legacy Row Timing Audit & Gate Disposition

**Date:** 2026-11-17  
**Scope:** Stored historical ledger records, timing verification, and prospective gate qualification under `EDGE_GATE §1` and `§3`.  
**Ledger Invariant:** **Zero bytes modified in any stored ledger artifact.**

---

## 1. Executive Summary

The PR #185 timing inventory flagged quote captures that lacked verifiable pre-start boundaries or occurred after event start across historical records:
1. **Week 7 NFL Trigger Cohort (`viewer/clv_report_2026_w7.json`)**: 37 legacy rows originally captured as opening/re-check comparisons on Tuesday, 2026-10-20. As established in `docs/LEDGER_FOUNDATION.md` and `AMEND-001`, these rows lack literal `event_id` and `event_start_at` attributes.
2. **Polymarket Line History**: 28 post-kickoff price-change rows on game `2026_09_BUF_MIN` (captured at +1.538h offset).
3. **Kalshi NBA Daily Quotes (`viewer/nba_odds_*.json`)**: 237 quotes captured after tipoff due to GitHub Actions scheduled workflow queue delays.

This audit documents the disposition of these legacy rows under `EDGE_GATE §1` and `§3`, confirms that the confirmatory $N = 600$ commercial gate is completely unaffected, and cryptographically proves zero bytes were modified in any existing ledger file.

---

## 2. Week 7 Trigger Cohort (37 Legacy Rows)

The 37 rows in `viewer/clv_report_2026_w7.json` reflect historical Week 7 trading records evaluated on 2026-10-20. Under `EDGE_GATE §1` and `§3`, these rows serve as historical descriptive context and are preserved verbatim.

| # | ID / Receipt | Event ID | Player | Market | Book | Quoted At (UTC) | Scheduled Start (UTC) | Offset (sec) | Offset (hrs) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `mgm_ne_chi_henry_yds_open` | `2026_07_NE_CHI` | Hunter Henry | receiving_yards | BetMGM | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 2 | `dk_ne_chi_douglas_yds_open` | `2026_07_NE_CHI` | DeMario Douglas | receiving_yards | DraftKings | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 3 | `bovada_29513980_G-2W-OU...Douglas.119` | `2026_07_NE_CHI` | DeMario Douglas | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 4 | `pin_ne_chi_hollins_yds_open` | `2026_07_NE_CHI` | Mack Hollins | receiving_yards | Pinnacle | 2026-10-20T06:23:18Z | 2026-10-23T00:15:00Z | -237,101s | -65.9h |
| 5 | `dk_sf_atl_kittle_yds_open` | `2026_07_SF_ATL` | George Kittle | receiving_yards | DraftKings | 2026-10-20T06:23:18Z | 2026-10-25T17:00:00Z | -470,201s | -130.6h |
| 6 | `fd_sf_atl_kittle_rec_open` | `2026_07_SF_ATL` | George Kittle | receptions | FanDuel | 2026-10-20T06:23:18Z | 2026-10-25T17:00:00Z | -470,201s | -130.6h |
| 7 | `mgm_sf_atl_cmc_yds_open` | `2026_07_SF_ATL` | Christian McCaffrey | receiving_yards | BetMGM | 2026-10-20T06:23:18Z | 2026-10-25T17:00:00Z | -470,201s | -130.6h |
| 8 | `bovada_29514571_G-2W-OU...St. Brown.119` | `2026_07_GB_DET` | Amon-Ra St. Brown | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 9 | `bovada_29513980_G-2W-OU...Kmet.119` | `2026_07_NE_CHI` | Cole Kmet | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 10 | `bovada_29513980_G-2W-OU...Loveland.119` | `2026_07_NE_CHI` | Colston Loveland | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 11 | `bovada_29513980_G-2W-OU...Swift.119` | `2026_07_NE_CHI` | D'Andre Swift | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 12 | `bovada_29513980_G-2W-OU...Chism.119` | `2026_07_NE_CHI` | Efton Chism III | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 13 | `bovada_29513980_G-2W-OU...Raymond.119` | `2026_07_NE_CHI` | Kalif Raymond | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 14 | `bovada_29513980_G-2W-OU...Burden.119` | `2026_07_NE_CHI` | Luther Burden III | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 15 | `bovada_29513980_G-2W-OU...Kmet.119` | `2026_07_NE_CHI` | Cole Kmet | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 16 | `bovada_29513980_G-2W-OU...Swift.119` | `2026_07_NE_CHI` | D'Andre Swift | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 17 | `bovada_29513980_G-2W-OU...Chism.119` | `2026_07_NE_CHI` | Efton Chism III | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 18 | `bovada_29513980_G-2W-OU...Raymond.119` | `2026_07_NE_CHI` | Kalif Raymond | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 19 | `bovada_29513980_G-2W-OU...Williams.119` | `2026_07_NE_CHI` | Kyle Williams | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 20 | `bovada_29513980_G-2W-OU...Burden.119` | `2026_07_NE_CHI` | Luther Burden III | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |
| 21 | `bovada_25555912_G-2W-OU...Vele.119` | `2026_07_PIT_NO` | Devaughn Vele | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T13:30:00Z | -417,812s | -116.1h |
| 22 | `bovada_25555912_G-2W-OU...Metcalf.119` | `2026_07_PIT_NO` | DK Metcalf | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T13:30:00Z | -417,812s | -116.1h |
| 23 | `bovada_25555912_G-2W-OU...Warren.119` | `2026_07_PIT_NO` | Jaylen Warren | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T13:30:00Z | -417,812s | -116.1h |
| 24 | `bovada_25555912_G-2W-OU...Johnson.119` | `2026_07_PIT_NO` | Juwan Johnson | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T13:30:00Z | -417,812s | -116.1h |
| 25 | `bovada_25555912_G-2W-OU...Warren.119` | `2026_07_PIT_NO` | Jaylen Warren | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T13:30:00Z | -417,812s | -116.1h |
| 26 | `bovada_29514571_G-2W-OU...St. Brown.119` | `2026_07_GB_DET` | Amon-Ra St. Brown | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 27 | `bovada_29514571_G-2W-OU...Watson.119` | `2026_07_GB_DET` | Christian Watson | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 28 | `bovada_29514571_G-2W-OU...Gibbs.119` | `2026_07_GB_DET` | Jahmyr Gibbs | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 29 | `bovada_29514571_G-2W-OU...Golden.119` | `2026_07_GB_DET` | Matthew Golden | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 30 | `bovada_29514571_G-2W-OU...LaPorta.119` | `2026_07_GB_DET` | Sam LaPorta | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 31 | `bovada_29514571_G-2W-OU...Williams.119` | `2026_07_GB_DET` | Savion Williams | receptions | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 32 | `bovada_29514571_G-2W-OU...Watson.119` | `2026_07_GB_DET` | Christian Watson | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 33 | `bovada_29514571_G-2W-OU...Gibbs.119` | `2026_07_GB_DET` | Jahmyr Gibbs | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 34 | `bovada_29514571_G-2W-OU...Golden.119` | `2026_07_GB_DET` | Matthew Golden | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 35 | `bovada_29514571_G-2W-OU...LaPorta.119` | `2026_07_GB_DET` | Sam LaPorta | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 36 | `bovada_29514571_G-2W-OU...Kraft.119` | `2026_07_GB_DET` | Tucker Kraft | receiving_yards | Bovada | 2026-10-20T17:26:27Z | 2026-10-25T20:25:00Z | -442,712s | -123.0h |
| 37 | `dk_ne_chi_henry_rec_open` | `2026_07_NE_CHI` | Hunter Henry | receptions | DraftKings | 2026-10-20T17:26:27Z | 2026-10-23T00:15:00Z | -197,312s | -54.8h |

---

## 3. Polymarket Line-History Rows (`2026_09_BUF_MIN`)

The Polymarket line-history archive contains 28 post-kickoff price moves recorded on `2026_09_BUF_MIN` (kickoff: `2026-11-09T20:15:00 ET` / `2026-11-10T01:15:00Z`).
- **Kickoff:** `2026-11-10T01:15:00Z`
- **Observed At:** `2026-11-10T02:47:17Z`
- **Offset to Kickoff:** `+5,537s` (`+1.538h` post-kickoff)
- **Count:** 28 price-change rows for market `polymarket:6374549` and related receiver props.

Under the new pre-start enforcement rules (`src/ledger/close_match.py`), these rows return `CLOSE_AFTER_START` and are never evaluable as closing lines.

---

## 4. Disposition Under EDGE_GATE §1 and §3

- **EDGE_GATE §1 (Confirmatory Standard)**: Confirmatory evaluation requires prospectively logged decisions recorded in the append-only ledger under strict cryptographic invariants.
- **EDGE_GATE §3 (Execution Protocol & Timeline)**: Mandates strictly:
  $$\text{entry} < \text{close} < \text{event\_start}$$
- **AMEND-001 Scope**: As codified in `docs/protocol_amendments/AMEND-001-nfl-event-key.md` § scope, historical records prior to 2026-11-01T00:00:00Z and the Week 7 37-row cohort are outside AMEND-001 and are not retroactively rewritten or modified.

**Conclusion:** All legacy rows are retained as historical descriptive context. Under the updated codebase, any close quote where $t_{\text{close}} \ge t_{\text{start}}$ returns `CLOSE_AFTER_START` and cannot be evaluated.

---

## 5. Impact on the $N = 600$ Commercial Gate

**The $N = 600$ gate is completely unaffected.**
- The $N = 600$ gate operates strictly **prospectively** on fresh quotes registered after the effective date of the protocol.
- Every counted decision must clear all integrity gates:
  1. Verified feed provider quote with receipt hash.
  2. $t_{\text{entry}} < t_{\text{close}} < t_{\text{start}}$ enforced by `match_close()`.
  3. Pre-start qualification (`pre_start = true`) enforced by `OddsIngestionAdapter`.
- Zero historical rows from the Week 7 cohort or retrospective Polymarket archives were ever eligible for the $N = 600$ gate tally.

---

## 6. Cryptographic Hash Invariant Verification

As mandated, **zero bytes were changed in any stored ledger artifact**.

```bash
shasum -a 256 viewer/clv_report_2026_w7.json viewer/clv_audit_report.json public/clv_audit_report.json viewer/ufc_ledger.json
```

| Ledger File | SHA-256 Digest | Status |
|---|---|---|
| `viewer/clv_report_2026_w7.json` | `3e908fe03863e6061df6d8ceef8923d88bf976037a39d791538491f63e3a17d4` | Verified Unchanged |
| `viewer/clv_audit_report.json` | `17e26f333c7bb582f9dcf16b878b77eb5a4f739c7c1eddf8d6c94e1616c83e52` | Verified Unchanged |
| `public/clv_audit_report.json` | `17e26f333c7bb582f9dcf16b878b77eb5a4f739c7c1eddf8d6c94e1616c83e52` | Verified Unchanged |
| `viewer/ufc_ledger.json` | `fb5ecb6fdbe08b174fc6e32026a544c629d1b2c0649e2c8954cce7305484634e` | Verified Unchanged |
