# Close-capture reference-time audit — 2026-11-19

## Scope and immutable evidence

The append-only `data/close_quality_captures.jsonl` contained 45 implicit
schema-v1 Kalshi records when this audit began (SHA-256
`2e9a0757f24d758b56fd7a8cbe43dabf832da6fbfbcfd27bc34a39620eb76745`).
This PR does not rewrite those lines or their hash chain. It does not alter
decision-time `pre_start`, qualification, the ledger, or EDGE_GATE values.

In v1, `offset_minutes` and `pre_start` used Kalshi `updated_time` or the
Polymarket book timestamp (`quoted_at`) when present. Those values describe
the provider's last quote update, while bid, ask, and depth were fetched at
`captured_at`. **Schema-v1 `pre_start` must not be used for analysis.** The
v2 analysis helper excludes v1 records. Historical coverage below is audited
from raw `captured_at` and scheduled start, without trusting v1 `pre_start`.

All 16 BOS–DEN records below were captured at `2026-11-19T02:33:11.371218Z`,
after the scheduled `02:30:00Z` start, but stored `pre_start: true`:

| Misflagged record ID | Misflagged record ID |
|---|---|
| `6c5551bc08f963b670eb6031` | `62ec62c5097818dbaa2960e6` |
| `ced1d36659be4d20e7444938` | `cb971f54cb6a41ead820ab2a` |
| `adbe529906e72791190edda4` | `dbc54cd749ff0b38208ce37d` |
| `1133709a19874a2bb9844414` | `21fcd9c442e398f3b608c8fd` |
| `30e2a490cb88801abeb9fa24` | `122e72b745ad5dbc7c8eb261` |
| `cfd457e311c848d1d1dfa8b2` | `1382fa819a9a4d8b51c01602` |
| `c9ae26924dded5638e6dd4dd` | `edba85cd3e08d247f80860d7` |
| `bf7f99abd6df06e4d120901c` | `628a90eeb00e62143fc0dc92` |

New v2 records derive `offset_minutes` and strict `pre_start` from
`captured_at`, record `quote_age_minutes = captured_at - quoted_at`, and name
`volume_window_method = successive_volume_total_delta` only when a previous
v2 capture of the same source and market has a valid cumulative volume. The
first v2 capture has a null window. A volume reset also yields null rather
than an estimated zero. `record_id` includes `captured_at` in v2 so an
unchanged provider book observed at a new time remains a distinct capture.
The live loop stamps each record after its market and order-book reads,
instead of reusing the run-start clock across sequential fetches.
The same reference-time fix applies to Kalshi and Polymarket normalization.

## Executed coverage versus intended cadence

The close-quality workflow's cron targets six ticks per hour. For the Nov 18
NBA evening (`2026-11-18T23:03Z` through `2026-11-19T05:53Z`), that is **42
nominal ticks**. GitHub run history contains **one scheduled run**, created
at `2026-11-19T02:32:58Z` (run `44445125368`), which wrote all 45 records
at 02:33:11Z. The only earlier successful manual run was at 21:06Z, before
the capture window for the early games. The #206 news-watcher backstop first
ran at 15:51Z on Nov 19, after this slate. Run history proves that no
capture workflow executed in the early games' eligibility windows; it does
not expose GitHub's internal reason for the missing scheduled executions.

Counts below use **captured_at**, with non-overlapping windows T-60 =
[45,75) minutes before start, T-30 = [25,45), T-15 = [10,25), and T-5 =
[0,10). Post-start captures are separate. These are per-market records,
not independent workflow runs.

| Nov 18 NBA game (start UTC on Nov 19) | T-60 | T-30 | T-15 | T-5 | Post-start |
|---|---:|---:|---:|---:|---:|
| HOU–IND (00:00) | 0 | 0 | 0 | 0 | 0 |
| MEM–CLE (00:00) | 0 | 0 | 0 | 0 | 0 |
| MIL–MIA (00:00) | 0 | 0 | 0 | 0 | 0 |
| PHI–ORL (00:00) | 0 | 0 | 0 | 0 | 0 |
| CHI–BKN (00:30) | 0 | 0 | 0 | 0 | 0 |
| WAS–TOR (00:30) | 0 | 0 | 0 | 0 | 0 |
| ATL–OKC (01:00) | 0 | 0 | 0 | 0 | 0 |
| DET–NOP (01:00) | 0 | 0 | 0 | 0 | 0 |
| GSW–DAL (01:00) | 0 | 0 | 0 | 0 | 0 |
| BOS–DEN (02:30) | 0 | 0 | 0 | 0 | 16 |
| PHX–SAC (03:00) | 0 | 15 | 0 | 0 | 0 |
| UTA–LAL (03:00) | 0 | 14 | 0 | 0 | 0 |

The 00:00Z and 01:00Z games produced no records because the 21:06Z manual
run was more than 75 minutes before their starts and the next executed run
was at 02:33Z, more than 10 minutes after their starts. The capture code
intentionally considers only scheduled starts from 75 minutes before to 10
minutes after. This establishes the direct cause from code and run history;
the provider did not get a fetch attempt for those games in that interval.

As of this Nov 19 daytime audit, Thursday night's `2026-11-19T23:03Z` to
`2026-11-20T05:53Z` window has **not begun**. It has 42 nominal scheduled
ticks, zero executed ticks so far, and no recorded captures for the four
01:00Z NBA games (GSW–MEM, LAC–SAS, NYK–MIN, POR–MIL) or NFL IND–HOU at
01:15Z. Every T-60/T-30/T-15/T-5 cell is therefore **pending**, not a miss.
The #206 backstop did dispatch successful daytime captures at 15:51Z and
16:22Z, outside these games' eligibility windows. Recompute this table after
the games; a 30-minute backstop does not certify T-15 or T-5 coverage.

## PR #204 review (left unmerged)

PR #204 scopes dispatch to one named game, player, receiving-yards market,
and `Polymarket` book; carries a provider side token and scheduled start;
records terms only after webhook success; and adds a manual CLOB re-read at
recipient open (`exact`, `moved`, `suspended`, `unknown`). Its PR reports 39
targeted tests and 701 Python tests. No real dispatch or recipient open was
observed. The workflow is disabled while `PROPSTEIN_EDGES_URL` or any pilot
scope variable is unset; currently all are unset, and no Discord/Telegram
delivery secret is configured. The static site does not host `/v1/edges`.

Within the trusted canonical `/v1/edges` path from merged PR #192, an alert
cannot arise from a quote lacking the explicit verified flag, receipt,
verification clauses, fresh provider time, book line/price, and passing
policy. The dispatcher repeats key output checks. It does **not** verify a
cryptographic receipt itself: its trust boundary is the configured HTTPS
endpoint's `source: verified_feed` claim. A wrongly configured or compromised
endpoint could assert those fields. The new pilot also assumes a Polymarket
outcome token identifies the exact offered line and that integer American
odds equality represents an exact price; its manual probe does not establish
that a recipient actually opened the alert.

The material conflict with #192's release hold remains: `verification.v1`
requires two current approved sharp-sportsbook quotes for its conflict
clause, and Polymarket is not an approved sharp-book peer. A single
Polymarket-only pilot cannot produce a real VERIFIED decision under that
contract. No sharp peer or public API host has been added by #204. Keep it
open; do not enable or merge it as a live pilot on fixture-only tests.

## Week 11 weather and open PRs

The Nov 19 ESPN ingest dry run again returned no `weather_observed_at`,
temperature, or wind for outdoor MIA–BUF at Highmark Stadium or MIN–SF at
Estadio Banorte. The provenance check remains 11/13 ready and blocked on
those two games. No provider, synthetic observation, or gate exception was
introduced. Nick's options for Mexico City are to wait for real ESPN weather;
hold its pricing; or approve a separate, reviewed event-exclusion policy if
ESPN never supplies the fields. The last option is **not** part of this PR.

Open PR housekeeping at audit time: #204 remains under its explicit live
release hold. #196 changes prospective CLV close policy (effective
`2026-12-01T00:00Z`) and remains open pending §14 threshold selection. Draft #185
explicitly leaves §14 numerical slots unfilled and says not to merge. The
mobile league-logo PR #207 was merged separately after its seven non-weather
checks passed; the same weather check remained red.
