# Close-quality inventory, November 2026

Read-only survey of the working tree at `0484e1c81f39a86e42733b45b5cd84e27209a384`.
It records where Kalshi and Polymarket snapshot and close fields actually sit,
what each stored price is, and how each stored timestamp sits against scheduled
event start. It does not choose a floor, a reference, a cutoff offset, or a
close-price definition. Those stay as slots in `docs/EDGE_GATE.md` §14.

DFS break-evens are out of scope. The `evaluation.break_even_adv` field on a
Polymarket quote is a model-minus-threshold figure from
`scripts/fetch_polymarket_props.py` (`compute_evaluation`), not a DFS close
and not a venue close.

## Provenance tags

Each liquidity or price field is tagged with one of:

| Tag | Meaning |
|---|---|
| `platform_api` (raw) | Stored as returned by the venue payload the writer read. |
| `derived` | Computed from venue fields, or from other stored fields, before it was written. |
| `imputed` | Filled by a constant or an authored stand-in when a venue observation was not used. |
| `null_by_venue_limitation` | The venue cannot supply the field. |

`null_by_venue_limitation` is not used in this note. The writers do not read
close-window volume, open interest, or last trade price, and this survey did
not capture a live venue payload. Absence from the file is not evidence the
venue lacks the field.

A fifth label, **writer clock**, is used when the timestamp is the ingestion
process clock. That is not a venue field, so none of the four tags apply.

## Do not treat these as one liquidity metric

Kalshi and Polymarket do not store the same liquidity quantity.

- Polymarket's stored depth, where it exists, is top-of-book ask notional:
  price times size summed only on the best ask of that outcome token
  (`scripts/fetch_polymarket_props.py` lines 162–172). Bids are not parsed.
  Levels behind the best ask are discarded. `liquidity_usd` is the minimum of
  the two tokens' top-of-book ask notionals (line 278). That is not traded
  volume and not a full book.
- Kalshi's published files store no depth. The writer reads `yes_bid_dollars`,
  `yes_ask_dollars`, and `no_ask_dollars` (`price()`, lines 143–147) and uses
  `yes_ask - yes_bid` only as an in-memory filter (`main_lines`, lines
  156–158). That gap is a probability spread on the YES contract. It is not
  dollar depth, and it is not written to the file.
- Neither store has close-window traded volume or open interest.

Those quantities are not comparable and are not pooled.

## Stores that are not a Kalshi or Polymarket close

| Path | What it is |
|---|---|
| `viewer/nba_odds_2026-10-22.json`, `2026-10-23`, `2026-10-29` | `feed_status` `fixture`, book `PropWire Fixture`. Not Kalshi. |
| `viewer/nba_odds_2026-10-31.json` through `2026-11-07.json` | `source` `the-odds-api`, book `FanDuel`. Not Kalshi. |
| `live_odds_feed.json` (repo root) | `feed_origin` `bundled_demonstration_fixture`. Synthetic books. Not Polymarket. |
| `snapshots/snapshots_2026_w1.jsonl` (862), `snapshots_2026_w7.jsonl` (1118), `snapshots_2026_w8.jsonl` (472), `snapshots/full_board_2026_w8_early_1300.jsonl` (472) | Model journals. A text search found no `kalshi` or `polymarket` string. |
| `viewer/precommit_log.jsonl` | Empty file. |
| `data/market_edges.db` table `closing_snapshots` | 20 rows, all book `Bovada`. Zero Kalshi or Polymarket rows. The sqlite table has `locked_at` and no kickoff column. |
| `data/line_history.db`, `data/propwire_agent_truth.db`, `picks_log.jsonl`, any `*ledger*.jsonl` | Not in the tree. `src/ledger/export.py` can write a JSONL mirror (lines 13–37: id, line, `quote_source`, `observed_at` only). No mirror file is committed, so N for that mirror is 0. |
| `viewer/feeds/feed_heartbeat.json` | Last-success time and quote count for NBA. No prices. The NBA stamp matches `viewer/nba_odds_2026-11-16.json`. |

There is no `nba_odds` file for 2026-11-08 through 2026-11-12.

## Kalshi (NBA)

### Path, writer, schema

| | |
|---|---|
| Paths | `viewer/nba_odds_<slate date>.json`, sibling `viewer/nba_games_<slate date>.json` (ESPN schedule, not prices), sibling `.manifest.json` |
| Writer | `scripts/fetch_kalshi_nba.py` `write_files` (lines 220–239), via `validate_and_publish` and `LiveOddsFeed`. Scheduled from `.github/workflows/nba_daily.yml` and `daily_update.yml`. |
| Published quote fields | `quote_id`, `provider_quote_id`, `player_id`, `player_name`, `team`, `opponent`, `game_id`, `market`, `line`, `over_odds`, `under_odds`, `book`, `observed_at`, `source`, `source_url`, `verified`, `_fixture`, `unit`. Enumerated from the four Kalshi files. |
| Header | `source` `kalshi`, `feed_status` `live-unverified`, `monitored_books` `["Kalshi"]`. |

The writer also sets `over_ask_price` and `under_ask_price` (lines 198–199).
`OddsQuote` (`src/contracts/odds.py` lines 25–48) has no ask fields, and
`validate_and_publish` serializes the validated model (`src/contracts/gate.py`
line 117). The published files do not contain `over_ask_price`,
`under_ask_price`, bid, spread, mid, volume, or depth. The raw ask does not
survive publication.

`line` is `floor_strike` from the market payload (line 183): `platform_api`
(raw). One row per player per file (the threshold whose in-memory mid is
closest to one half, lines 162–164). Market is `points` only.

### What the stored price is

`over_odds` is `cents_to_american` of `yes_ask_dollars`. `under_odds` is
`cents_to_american` of `no_ask_dollars` (lines 196–197). `cents_to_american`
(lines 74–79) clamps the probability and rounds to integer American odds, so
the stored number is `derived` and is not the raw ask. It is not the last
trade. It is not the mid. The mid is computed only to choose which strike to
keep, and it is not stored (`derived`, then discarded).

`observed_at` is `datetime.now` at fetch (line 271), copied onto every quote
in that file. Writer clock. One file is one capture instant, overwritten on
the next successful publish for that slate date. It is not a last trade time
and not a cutoff anchored to tip.

The manifest field `data_cutoff_timestamp` (for example
`viewer/nba_odds_2026-11-16.manifest.json`) is the same publish clock. The
name is not an event-relative cutoff.

### Date range, N, and timestamp versus tip

Tip is `tipoff_utc` on the sibling `nba_games_<date>.json`. Comparison is
per quote, that quote's `game_id` against that game's tip. Positive hours
mean the capture is after tip.

| File | Capture (`observed_at`, one value) | Quotes | Before tip | After tip | Hours, capture minus tip (min / median / max) |
|---|---|---:|---:|---:|---|
| `viewer/nba_odds_2026-11-13.json` | 2026-11-14T02:33:21Z | 115 | 30 | 85 | −0.944 / 1.056 / 2.556 |
| `viewer/nba_odds_2026-11-14.json` | 2026-11-15T02:00:42Z | 62 | 0 | 62 | 1.012 / 1.012 / 2.012 |
| `viewer/nba_odds_2026-11-15.json` | 2026-11-16T02:17:05Z | 24 | 0 | 24 | 0.285 / 1.285 / 2.285 |
| `viewer/nba_odds_2026-11-16.json` | 2026-11-17T00:43:14Z | 110 | 44 | 66 | −1.279 / 0.221 / 0.721 |
| Total | 4 capture instants | 311 | 74 | 237 | |

Zero quotes have `observed_at` equal to tip. Because each file has one clock,
a single publish is before some tips and after others on the same slate.

Rows captured after scheduled tip (flagged; not re-tagged):

- 2026-11-13, capture 2026-11-14T02:33:21Z: NYK-IND 10 quotes (+2.556 h), ATL-CHI 15 (+1.556 h), CLE-MIA 15 (+1.556 h), MEM-NOP 14 (+1.556 h), UTA-HOU 16 (+1.056 h), GSW-SAS 15 (+0.056 h). Before tip: POR-SAC 14 (−0.444 h), OKC-LAC 16 (−0.944 h). MIL-ORL has no quote; the capture clock was 2.556 h after its tip.
- 2026-11-14, capture 2026-11-15T02:00:42Z, all 62 quotes after tip: DEN-NOP 10, DAL-CLE 11, BKN-MEM 10, CHI-MIN 15, DET-MIA 16. Offsets 1.012 h to 2.012 h.
- 2026-11-15, capture 2026-11-16T02:17:05Z, all 24 quotes after tip: HOU-WAS 1, ATL-MIL 13, OKC-SAC 10. Offsets 0.285 h to 2.285 h. Six scheduled games have no quote (CHA-LAC, DAL-BOS, IND-PHI, LAL-PHX, NYK-TOR, SAS-UTA).
- 2026-11-16, capture 2026-11-17T00:43:14Z: after tip, CHI-CLE 13, MEM-WAS 14, ORL-BOS 14 (+0.721 h), HOU-MIA 12, PHI-BKN 13 (+0.221 h). Before tip: GSW-NOP 13, POR-MIN 16 (−0.279 h), PHX-DEN 15 (−1.279 h).

### Field table (published Kalshi row)

| Field | In the file | Provenance |
|---|---|---|
| Close-window traded volume | Absent | Not read. Not `null_by_venue_limitation`. |
| Open interest | Absent | Not read. Not `null_by_venue_limitation`. |
| Bid | Absent | `platform_api` (raw) `yes_bid_dollars` is read in memory and dropped. |
| Ask | Absent as a price. American `over_odds` / `under_odds` are present. | `derived` from `yes_ask_dollars` and `no_ask_dollars`, then clamped and rounded. Raw ask dropped at publication. |
| Spread at cutoff | Absent | `derived` in memory as `yes_ask - yes_bid` for the strike filter only. Not a cutoff spread. Not stored. |
| Last trade price | Absent | Not read. Not `null_by_venue_limitation`. |
| Mid | Absent | `derived` in memory as `(bid + ask) / 2` to pick the strike. Not stored. |
| Cutoff / close timestamp | Absent | `observed_at` is the writer clock. Manifest `data_cutoff_timestamp` is that same clock. |
| Order-book depth | Absent | Not read. |
| Price impact at a notional | Absent | Not computable. No depth is stored. |

### Coverage gaps

Four slate dates, one capture each, not a series through the day. Earlier
publishes of the same path are overwritten. 237 of 311 quotes are after that
quote's tip. Several scheduled games have no quote. No bid, ask, spread, mid,
last trade, volume, open interest, or depth remains in the file. Exact spread
is not recoverable from the rounded American odds, and those odds are the YES
ask and the NO ask, not a bid and an ask on one side.

## Polymarket (NFL), current feed

### Path, writer, schema

| | |
|---|---|
| Path | `viewer/live_odds_feed.json` and `viewer/live_odds_feed.manifest.json` |
| Writer | `scripts/fetch_polymarket_props.py` `merge_feed`, via `PolymarketMergedFeed` (`src/contracts/polymarket.py` lines 19–36, `extra` allowed, so ask and depth fields survive). Scheduled from `.github/workflows/odds_freshness.yml`. |
| This file | One overwritten snapshot. `feed_status` `LIVE_POLYMARKET_CLOB_RECEIPTED`. `updated_at` 2026-11-16T20:55:14.460005Z. 4 quotes, all book `Polymarket`, all `game_id` `2026_10_LAC_BAL`. Markets: receiving yards 2, receptions 2. |

Quote price fields present: `over_ask_price`, `under_ask_price`,
`over_ask_depth_usd`, `under_ask_depth_usd`, `liquidity_usd`, `over_odds`,
`under_odds`, `observed_at`, `end_date`. No bid, spread, mid, last trade,
volume, or open interest key.

### What the stored price is

`over_ask_price` and `under_ask_price` are the minimum ask price on that
token's CLOB book: `platform_api` (raw). `over_odds` and `under_odds` are
`cents_to_american` of those asks (lines 76–81 and 277): `derived`, clamped
and rounded. Not a last trade. Not a mid. The fetcher does not compute a mid.

`over_ask_depth_usd` and `under_ask_depth_usd` are `derived`: notional at the
best ask only (lines 170–172). `liquidity_usd` is `derived`: the min of those
two (line 278). On these 4 quotes, `liquidity_usd` runs from 11.27 to 51.15.
One side's top-of-book ask notional on the same file is as large as 864.81.
That is fetch-time top-of-book ask notional, not close-window volume, and not
full-book depth.

`observed_at` is the writer clock, with microseconds added per quote inside
one fetch (`merge_feed`, lines 633–635). `end_date` is Gamma
`market.endDate`: `platform_api` (raw). It is the market's end timestamp, not
a preregistered offset before start.

The manifest `data_cutoff_timestamp` is 2026-11-16T20:55:14.465838Z, the
publish clock, not kickoff.

### Timestamp versus kickoff

Kickoff is built the same way as `scripts/grade_nfl_week.py` lines 85–86:
`gameday` plus `gametime` in `America/New_York`, from
`viewer/games_2026_w10.json`. For `2026_10_LAC_BAL` that is gameday
2026-11-16, gametime 20:15, which is 2026-11-17T01:15:00Z.

All 4 quotes are 4.329 hours before that kickoff. None are at or after start.
On these 4 quotes, `end_date` equals that kickoff (0 hours apart). That
equality is this file only. It is not a stored rule that Gamma `endDate` is
scheduled start, and it is not `[CUTOFF_OFFSET_SLOT]`.

## Polymarket line history (retained series)

| | |
|---|---|
| Path | `viewer/api/lines/<player_id>/<market>.json` (355 files) |
| Writer | `src/line_history.py` `export_static_series`, fed by `ingest_quotes` from `viewer/live_odds_feed.json` in `.github/workflows/odds_freshness.yml`. `data/line_history.db` is not in the tree (the workflow caches it). The JSON export is the retained copy. |
| Point fields | `t`, `line`, `over`, `under`, `verified`. Nothing else (`get_series`, lines 346–351). |
| Dedupe | A repeat of the same line and both prices is not inserted (`record_line`, lines 218–227). |

Polymarket points: 6,202, across 47 distinct UTC seconds, from
2026-11-04T09:58:33.696126Z through 2026-11-16T20:55:14.460008Z. Each point's
`t` is unique at microsecond resolution because the feed stamps quotes that
way. 47 seconds is the count of capture instants that changed a stored price.
`over` and `under` are the feed's American odds: `derived`. `t` is the feed's
writer clock. `verified` is false on these points. Ask prices and depth are
dropped, so spread and price impact are not computable from this series.

Ten other points are synthetic books from 2026-10-20. They are not Polymarket.

### Timestamp versus event start

A line-history point has no `game_id` and no event start. Before/after is not
a stored fact. The join used here is: `player_id` to
`viewer/slate_2026_w*.json` and `viewer/games_2026_w*.json`, kickoff built as
in `grade_nfl_week.py`. Slates are partial (week 8 has 30 players, week 9 has
29, week 10 has 27), so most captures have no slate game near them.

| Join result | Points | How this relates to start |
|---|---:|---|
| Player has a later slate kickoff | 441 | All are before that kickoff. Hours before: min 4.329, median 90.264, max 303.274. The median is days, not a close window. |
| Timestamp is after the player's last slate kickoff in the repo | 5,154 | Hours after: min 1.538, median 414.662, max 1,514.859. 5,126 of these are more than 24 hours later. That is a missing later slate, not an in-game print. |
| Player is on no slate that has a games file | 607 | Relation to start is unknown. |
| Inside `[kickoff, kickoff + 8 hours]` of a slate game for that player | 28 | These are the after-start rows that the join can actually support. |

The 28 after-start rows are one fetch: `t` at 2026-11-10T02:47:16Z, 6 players,
28 rows (more than one line per player), all on slate game `2026_09_BUF_MIN`
(week 9, gameday 2026-11-09, gametime 20:15). The capture is 1.538 hours
after that kickoff. No other Polymarket line-history point falls in the 8
hours after a known slate kickoff for that player.

Week 10 players, compared with the week 10 kickoff specifically
(2026-11-17T01:15:00Z, the only kickoff on that slate): 313 Polymarket points
for those players are before that kickoff (hours before: min 4.329, median
219.262, max 303.274) and 0 are after it. Every stored capture in this series
is before that kickoff because the latest `t` is 2026-11-16T20:55:14Z.

## Polymarket grade file

`viewer/feeds/grades/nfl_2026_w9.json`, written by `scripts/grade_nfl_week.py`.
Weeks 1, 7, and 8 grade files have no quote attached. Week 9 has 14 quoted
rows, all book `polymarket`.

`pregame_quote` (lines 54–68) keeps the latest line-history point with `t`
strictly before that week's kickoff. That is a last-available-price rule.
§14 does not use it. All 14 are before kickoff, by construction. Hours before
kickoff: 5.059 (nine rows), then 15.586, 23.174, 26.452, 54.994, 117.542.
The 28 after-start line-history rows are not in this file, because the filter
drops them. Stored fields on the quote are book, line, over, under,
`observed_at`. American odds, `derived`. No depth, bid, ask, spread, volume,
or open interest.

## Polymarket receipt log

| | |
|---|---|
| Path | `data/polymarket_receipt_log.json` |
| Writer | `scripts/fetch_polymarket_props.py` `append_receipts` (lines 411–418). Capped at 400 entries (line 54). |
| Fields | `receipt_id`, `market_id`, `book_id`, `raw_payload_hash`, `captured_at`, `source_url`, `payload_kind` (`receipt_entry`, lines 421–432). |
| N | 400 rows, all `book_id` `polymarket`. The file is at the cap, so older rows have been dropped. 96 distinct `captured_at` strings, 8 distinct UTC seconds, from 2026-11-15T19:51:31.817756Z through 2026-11-16T20:55:14.454215Z. |

No price, bid, ask, depth, volume, or open interest is stored. The hash is of
the canonical ask projection, not the prices. `captured_at` is the writer
clock. There is no event-start field, so before/after start cannot be
computed from this file. The date inside a market slug was not treated as a
start time.

## Ledger schema (no venue rows in the tree)

`db/migrations/001_ledger_foundation.sql` `closing_references` (lines
185–211) stores American odds, `observed_at`, line, and match status.
`db/migrations/003_quote_source.sql` adds `quote_source`. No volume, open
interest, bid, ask, spread, mid, last trade, or depth column. The JSONL
mirror writer does not even copy the odds (`src/ledger/export.py` lines
13–37). No mirror file and no database of ledger rows are in the tree. N is 0.
`src/agents/persistence.py` `clv_ledger` can store `closing_line`,
`closing_odds`, and `closed_at`. The sqlite file is not in the tree.

## Agent snapshot (not a venue capture)

`viewer/feeds/sniper_terminal_snapshot.json` is written by
`src/agents/graph.py` `export_static_snapshot` (lines 1371–1382).
`generated_at` is 2026-10-29T04:15:51.639434Z. `polymarket_board` has 6 rows
(file line 876). The first row has `yes_price` 0.525, `no_price` 0.485,
`spread_cents` 1.5, `liquidity_usd` 6850, which match the authored catalog in
`_default_polymarket_clob_catalog` (lines 251–254), not a stored venue
response. Tag for those prices, the spread, and that liquidity figure:
`imputed` (authored stand-in). `liquidity_score` on that row (0.9313) is
`derived` from `compute_clob_liquidity_score` (lines 103–117), a weighted
blend of a spread factor and a depth factor. That blend is not a §14 input.
§14 does not adopt a composite.

The same agent, when a field is missing on a raw market, fills defaults
(`src/agents/polymarket_agent.py` lines 227–247): a YES ask constant, a bid
from that ask minus a default spread, a NO ask from one minus the bid, a
liquidity constant, and a fee constant. Those fills are `imputed`. The
committed board matches the catalog, which supplies the fields, so those
fallback constants are not the numbers in the file. The board has no
scheduled start, so before/after start is not computable. These rows are not
counted in the venue N above. Ten `sniper_alerts` have `venue_type`
`polymarket`; they carry `odds_american` and `liquidity_score`, not a book,
not a close timestamp, and not depth levels.

## Field summary

| Field | Kalshi published file | Polymarket current feed | Polymarket line history |
|---|---|---|---|
| Close-window traded volume | Absent | Absent | Absent |
| Open interest | Absent | Absent | Absent |
| Bid | Absent (read, then dropped) | Absent | Absent |
| Ask | Absent; American odds `derived` from asks | `platform_api` (raw) best ask; American odds `derived` | Absent; American odds `derived` |
| Spread at cutoff | Absent | Absent as a field | Absent |
| Last trade price | Absent | Absent | Absent |
| Mid | Absent (used only to pick a strike) | Absent | Absent |
| Cutoff timestamp | Absent; writer clock | Absent; writer clock. `end_date` is Gamma end, not a cutoff offset | Absent; `t` is the writer clock |
| Order-book depth at close | Absent | Not at close. Fetch-time top-of-book ask notional only (`derived`) | Absent |
| Price impact at a notional | Absent | Absent | Absent |

`null_by_venue_limitation`: not applied. `imputed`: not used on the ingestion
files above. `imputed` applies to the agent snapshot catalog, which is not a
close store.

## Which §14 path these rows support

The fields-exist path needs the prospective field list (close-window volume,
open interest, bid, ask, spread at cutoff, last trade, mid, cutoff timestamp,
and order-book depth at cutoff) on historical rows. That list is not on the
stored Kalshi rows or the stored Polymarket rows.

Spread at cutoff is not stored. It is not recoverable from Kalshi history:
the raw ask is stripped, and the American odds are a clamped, rounded YES ask
and NO ask, not a bid-ask pair. On the current Polymarket feed the two best
asks are stored, so an ask-sum gap can be computed for those 4 quotes only.
That gap equals a token's bid-ask spread only if the missing bid equals one
minus the other token's ask, which is not stored and was not checked. The
line-history series drops the asks, so the gap is not computable across the
retained 6,202 points.

The rows on hand support the price-only path in §14.5. They do not support
executing the fields-exist path. Volume and price impact stay deferred until
a later amendment, after the field list has been recorded. This note does not
set that amendment's numbers.
