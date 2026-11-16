# NBA feed staleness, November 2026

Investigated from GitHub Actions run history, job logs, and the files those runs committed. The quota hypothesis is **confirmed** for The Odds API. It is not a 429, an auth rejection of a bad key, a schema change, or a workflow that was turned off.

## Last successful non-empty NBA Odds API poll

`2026-11-07T19:52:16Z`

- File: `viewer/nba_odds_2026-11-07.json` (`source` `the-odds-api`, `total_quotes` 85, book FanDuel, `_fixture` false).
- Commit: `3034786c123a0d6b0a91e5480e822d6946cf81e9` (`chore(data): NBA daily refresh 2026-11-07 [skip ci]`, 2026-11-07 19:52:17 UTC).
- Workflow: Daily NBA FanDuel Market Slate, run [42408264843](https://github.com/NickAltstein/Propstein/actions/runs/42408264843), success, created `2026-11-07T19:52:01Z`, head `13b36d10`.

There is no `viewer/nba_odds_2026-11-08.json` through `viewer/nba_odds_2026-11-12.json`. The board had no new NBA file for those five days.

## Error class after that poll

**Quota, surfaced as HTTP 401 `OUT_OF_USAGE_CREDITS`.** Not HTTP 429. The scheduler ran. The free `/events` call succeeded and listed games; the paid per-event odds call refused the key. `scripts/fetch_nba_odds.py` then raised `SystemExit`, and the job exited 1. It did not "succeed" on an empty body.

First failing scheduled run: [42518846438](https://github.com/NickAltstein/Propstein/actions/runs/42518846438), job `128847683145` (`refresh`, failure), `2026-11-08T20:11:29Z`, head `8aa3ab12d836ad978a0127598c54e212e9da5d1d`. Verbatim log line:

```
[fetch_nba_odds] HTTP 401 from /sports/basketball_nba/events/a5d2d9c68b7446860de658aa4bc33c5b/odds: b'{"message":"Usage quota has been reached. See usage plans at https://the-odds-api.com","error_code":"OUT_OF_USAGE_CREDITS","details_url":"https://the-odds-api.com/liveapi/guides/v4/api-error-codes.htm'
[fetch_nba_odds] 7 games across ['2026-11-08']; estimated cost 7 credits (cap 20)
##[error]Process completed with exit code 1.
```

The same `error_code` is in later failures:

- Run [42724544403](https://github.com/NickAltstein/Propstein/actions/runs/42724544403), job `129526687251`, `2026-11-09T23:57:19Z`, head `7390775c46693246fa27a1abcf4615c75837938f`, 8 games, same 401.
- Run [43321887087](https://github.com/NickAltstein/Propstein/actions/runs/43321887087), job `131544368640`, `2026-11-12T22:39:17Z`, head `c35df10504c7252c75265ab14299eb8592c55ffd`, 4 games, same 401.

Failed NBA daily runs in that window: 42518846438, 42522714536 (Nov 8), 42724544403, 42729181267 (Nov 9–10), 43110923052, 43119442353 (Nov 11), 43315139925, 43321887087 (Nov 12). All `conclusion: failure`.

The workflow file at those SHAs had no failure-webhook step. A red X in Actions was the only in-repo signal. Data Health did not exist yet (workflow `416916694` was created `2026-11-13T00:32:27-05:00`).

## What the board shows after Nov 13

Kalshi fallback landed in `ce5f92f5f` (`feat(nba): free Kalshi fallback for NBA player-points lines`). From Nov 14 the job can exit 0 while The Odds API is still refused.

Latest example: run [43791193762](https://github.com/NickAltstein/Propstein/actions/runs/43791193762), job `133049756927`, success, `2026-11-16T02:16:50Z`, head `dcd414f7e47fa772c8a4437113edf54afe3ce0b4`, commit `9b086a71cf8949675d10dda2a67377b354ab5541`. Verbatim:

```
[fetch_nba_odds] HTTP 401 from /sports/basketball_nba/events/488c2bc1e099361f06015cf126f95fd6/odds: b'{"message":"Usage quota has been reached. See usage plans at https://the-odds-api.com","error_code":"OUT_OF_USAGE_CREDITS","details_url":"https://the-odds-api.com/liveapi/guides/v4/api-error-codes.htm'
##[warning]The Odds API NBA fetch failed; using free Kalshi lines instead.
[kalshi_nba] 2026-11-15: 9 games (ESPN), 141 open Kalshi markets, 24 two-sided main lines across 3 games
```

`viewer/nba_odds_2026-11-15.json` is `source` `kalshi`, `updated_at` `2026-11-16T02:17:05Z`, 24 quotes. That is a real non-empty publish, and it is not FanDuel. The last FanDuel payload remains Nov 7.

Data Health run [43772551228](https://github.com/NickAltstein/Propstein/actions/runs/43772551228), job `132991471379`, `2026-11-15T23:01:59Z`, head `398f7d7017374f45cbcfdb3b8c1ae22fc43ad349`, failed on the key, not on the Kalshi file:

```
| `odds_api_credits` | ❌ FAIL | 0 credits remaining, 500 used this period; every Odds API job is down |
| `nba_lines` | ✅ OK | newest slate 2026-11-15, 106 quotes, updated 1.4h ago |
ALARM_WEBHOOK_URL secret not configured; alarm visible only in Actions history and owner email.
```

`scripts/fetch_nba_odds.py` documents the free plan as 500 credits/month. The balance header matches that cap, exhausted. Whether GitHub's own failure mail reached anyone is not in these logs. The repository webhook that would page a person was unset, which is why a week of red NBA runs did not page.

## NFL

The NFL board feed does **not** share this outage. Odds Feed Freshness keeps publishing Polymarket. Data Health on Nov 15 reported `nfl_odds_feed` OK (276 Polymarket quotes, 3.2h old, 6h limit) and `nfl_sportsbook` WARN with 0 sportsbook quotes. The sharp-book sweep in `scripts/fetch_nfl_props_sweep.py` uses the same `ODDS_API_KEY` and exits 0 on failure so Polymarket still publishes. Zero sportsbook rows are the shared quota symptom. The board's Polymarket lines are not.

The ~8–9h NFL staleness is not an off-day. Week 10 still has a Monday game: `viewer/games_2026_w10.json` game `2026_10_LAC_BAL`, `gameday` `2026-11-16`, `gametime` `20:15`. The latest Odds Feed Freshness success retrieved for this note is run [43853388782](https://github.com/NickAltstein/Propstein/actions/runs/43853388782), `2026-11-16T10:57:12Z`, head `bd8f51b2`. `viewer/live_odds_feed.json` `updated_at` is `2026-11-16T10:57:36.021803Z` with 2 Polymarket quotes (non-empty). From Nov 14 02:28 UTC through that run the workflow completed 11 times, all success, not the 15-minute cron in `.github/workflows/odds_freshness.yml`. That file already says GitHub drops or delays scheduled ticks. The 6h freshness alarm runs inside that same workflow, so a tick that never starts cannot page. No later run was in the listing at investigation time.

## Quota hypothesis

**Confirmed.** One Odds API key, 0 of 500 credits remaining, HTTP 401 `OUT_OF_USAGE_CREDITS` on every paid NBA odds call from Nov 8 through at least Nov 16. The poller was not disabled. Responses were not empty 200s.

## Repair

The paid Odds API is no longer called by scheduled jobs. NBA prices are Kalshi (`scripts/fetch_kalshi_nba.py`). NFL prices are Polymarket (`scripts/fetch_polymarket_props.py`). UFC cards refresh with `--no-odds`. Buying a larger Odds API plan was not done.

`ALARM_WEBHOOK_URL` is still unset in production (Data Health run 43772551228). The feed-age watch reads that same secret, plus optional `FEED_AGE_UPTIME_URL` for an external ping. Neither value belongs in the repo.
