# PropStein Edge Gate — Preregistered Statistical Specification

Status: **Preregistered governance document.** The parameters below are fixed
before any new prospective observations are collected for any sport. Any
change after registration is a protocol amendment, must be committed with its
own rationale BEFORE the affected data is examined, and never retroactively.

This document satisfies v3 directive TASK 2. It defines one evaluation per
sport (NFL, NBA, UFC). Nothing here authorizes commercial claims; passing a
gate is a necessary condition, and commercialization additionally requires
compliance approval (fail-closed, see §9).

---

## 1. Per-sport gates (summary)

| Parameter | Value |
|---|---|
| Confirmatory sample size | N = 600 independently evaluable qualifying signals **per sport** |
| Interim look at N = 300 | **Descriptive only** — no significance-based decision, no stopping |
| Effect threshold | Observed mean CLV ≥ **+1.0 percentage point** |
| Uncertainty rule | Lower confidence bound > 0 (see §6 for the exact bound) |
| Significance level | One-sided **α = 0.0125 per attempt** (see §5 multiplicity) |
| Attempts | **Maximum two prospective attempts per sport** |
| Second attempt | Must be registered before new observations are collected; the first dataset may NOT be reused as fresh confirmatory evidence |
| Clustering | Game-clustered bootstrap (cluster = game/event) with documented dependence and precision diagnostics |
| Cross-sport pooling | **Forbidden** for pass/fail decisions. NFL success authorizes nothing for NBA or UFC |
| Eligibility | Frozen at registration; no post-hoc modification |

## 2. CLV estimand (exact definition)

PropStein distinguishes two quantities that must never be blended:

1. **Price CLV (primary estimand — the gate statistic).** For a qualifying
   signal taken at side `s` of a market:

   ```
   CLV_i = p_fair(entry, s) − p_fair(close, s)
   ```

   where `p_fair(x, s)` is the **de-vigged (no-vig) implied probability** of
   side `s` at observation `x` on the same market, same line family, converted
   to a common line basis when the line moves (see §4). Positive `CLV_i` means
   the entry beat the close on the same side: the signal captured value before
   the market converged. The gate statistic is the arithmetic mean of `CLV_i`
   over evaluable signals, expressed in percentage points.

2. **Model-vs-close probability difference (diagnostic only).** The model
   fair probability minus the de-vigged market probability. This measures
   model disagreement with the market and is reported descriptively. It is
   NOT the gate statistic, is never substituted for price CLV, and never
   contributes to a pass decision.

De-vig method (prespecified): two-way proportional (multiplicative) removal
on the quoted pair; for markets with more than two outcomes, normalized
over-the-top removal is used and must be recorded per quote. American odds
convert to implied probability `p = a/(a+100)` for positive `a` and
`p = |a|/(|a|+100)` for negative `a` before de-vigging.

## 3. Qualification and evaluability

A signal qualifies when ALL of the following hold at registration time:

1. The candidate was registered by the Phase 1.5 ledger BEFORE the closing
   reference timestamp, with model-version and source provenance attached.
2. The entry quote comes from an **approved book** (list below) with a
   captured timestamp and an integrity hash.
3. The market is one of the sport's registered markets at registration.

A registered signal is **evaluable** only if a matching closing reference
exists for the same market, line, and quote source (§13):

- **Closing-source rule:** Until 2026-11-16 the registered text required a
  source separate from the model's input feed. That cell was never filled, so
  the gate stayed inactive and no signal was admitted under it. Effective
  2026-11-16 the close is a later observation with the same `quote_source`
  tag as the entry (§13). A different tag is unmatched. It is not independent
  verification, and it is not averaged into the entry's CLV.
- Missing, stale (beyond the per-sport staleness threshold), or mutually
  disagreeing closes (beyond the prespecified disagreement tolerance) make
  the signal **unevaluable**. Unevaluable signals are excluded from N with a
  reason code and reported in the qualification-rate report — they are never
  silently dropped and never counted as zeros.
- Pushes, voids, cancellations, DNP/non-participation: excluded from N with
  a reason code (they carry no CLV information; counting them as zero would
  bias the mean toward the null).

**Preregistered operating parameters (fill before first observation; blanks
make the gate inactive for that sport):**

| Field | NFL | NBA | UFC |
|---|---|---|---|
| Closing reference provider | `polymarket`. The close must carry that same tag (§13). | `kalshi`. The close must carry that same tag (§13). | None. This sport's gate stays inactive until an amendment names a source. |
| Approved entry books | `polymarket` only, for signals registered on or after 2026-11-16. | `kalshi` only, for signals registered on or after 2026-11-16. | None. |
| Capture timestamps (entry / close) | Entry: `intended_quotes.quoted_at`. Close: `closing_references.observed_at`. Both are UTC `timestamptz`. The entry must precede the close, and the close must precede `event_start_at`. | Same timestamps and order as NFL. | Not applicable while the UFC gate is inactive. |
| Line-change normalization rule | Exact line, rounded to 4 decimal places (`src/ledger/close_match.py`). A different line is `LINE_CHANGED` and unevaluable. The §4 translation is not applied to this sample. | Same rule as NFL. | Not applicable while the UFC gate is inactive. |
| Close-staleness threshold | verification.v1 quote age (`src/contracts/verification.py`): 90 minutes when the event is more than 30 minutes away or the start time is unknown; 15 minutes inside that 30-minute window; 2 minutes after the event has started. A quote older than the limit in force, or a close at or after `event_start_at`, is unevaluable. | Same limits as NFL. | Not applicable while the UFC gate is inactive. |
| Source-disagreement tolerance | 5.0 percentage points of de-vigged probability, and only between two closes that share the `polymarket` tag. A different tag is unmatched (§13), not a disagreement and not an average. One Polymarket close is not unevaluable for lack of a second publisher. | 5.0 percentage points of de-vigged probability, and only between two closes that share the `kalshi` tag. A different tag is unmatched (§13). One Kalshi close is not unevaluable for lack of a second publisher. | Not applicable while the UFC gate is inactive. |

A cell that names a source or a number is filled. A cell that says None or
Not applicable is also filled: it records that the UFC gate is inactive. An
empty cell would have the same effect, and this table no longer has one.

These values are registered on 2026-11-16 with the source switch, before any
prospective signal under them is admitted to N. Filling the table does not
requalify rows already stored. The thresholds in §1 are unchanged. Historical
`odds_api:<book>` rows stay in that source class (§13) and are not folded
into the Polymarket or Kalshi sample.

## 4. Line-change normalization

The sample registered in §3 does not use the translation below. A different
line is `LINE_CHANGED` and unevaluable. This section is the method a later
amendment would have to adopt before any translated close could enter N. It
is not in force for signals registered on or after 2026-11-16.

When the closing line differs from the entry line, the comparison uses the
closing line's market on the SAME side the signal took, evaluated at the
signal's recorded entry odds translated to the closing line via the standard
half-point/point-scaling rule for the market type (spread/total: interpolate
at 0.25-line granularity with documented pushes-to-zero handling; player
props: recompute from the closing de-vig pair at the closing line; discontinuous
markets where the line basis cannot be reconstructed are unevaluable). The
chosen rule per market family is recorded in the registration row so every
signal's normalization is auditable.

## 5. Multiplicity across attempts

Two prospective attempts per sport create a second false-positive
opportunity. To hold the overall one-sided family-wise error at 2.5%
(two-sided 5%) across both attempts, each attempt is tested at
**one-sided α = 0.0125**, equivalent to requiring the lower bound of the
98.75% one-sided confidence interval (a two-sided 97.5% interval) to exceed
zero. This is deliberately more conservative than applying 95% intervals
twice. A prespecified alternative multiplicity procedure may replace this
allocation only if documented and committed before the first attempt's data
is examined.

## 6. Decision rule

A sport's attempt PASSES iff, on the confirmatory N = 600 evaluable
signals:

1. observed mean price CLV ≥ +1.0 percentage point, AND
2. the lower bound of the game-clustered bootstrap interval (one-sided
   98.75%, i.e. the 1.25th percentile of the resampled mean distribution)
   is strictly greater than 0.

Any other outcome (including "inconclusive") **consumes the attempt**. A
second attempt requires a new registration, new observations, and may not
reuse the first attempt's dataset. If both confirmatory evaluations fail,
the sport's commercial actionable-signal product is retired; historical and
research features continue without claiming demonstrated predictive
advantage.

## 7. Interval construction and sensitivity analysis

- **Primary:** deterministic game-clustered bootstrap. Cluster = game/event
  id; resample clusters with replacement, 10,000 replicates, fixed seed
  `20261024`, percentile method. Because all signals within a game share
  game-script and availability dependence, cluster-level resampling keeps
  the interval honest; the same fixture re-runs bit-identically.
- **Sensitivity (mandatory, reported alongside):** (a) calendar-week block
  bootstrap (broader time blocks); (b) leave-one-cluster-out influence
  summary. If the primary conclusion does not survive the week-block
  sensitivity, the attempt is recorded as **inconclusive** (and therefore
  consumed).
- **Precision diagnostics (reported at the descriptive N=300 checkpoint and
  at N=600):** number of distinct clusters represented, signals per cluster
  distribution, effective sample size under cluster dependence, interval
  width. These exist to measure time-to-evidence honestly — they must never
  be used to loosen eligibility to finish faster (v3 §3).

## 8. Phase 1.5 reporting obligations

The weekly pipeline reports, per sport: rolling qualification rate
(qualified / screened), reason-code histogram for rejections, evaluable
percentage, clusters represented, current bootstrap interval width, and
model-version coverage. Weeks-to-evidence arithmetic (v3 §3) is monitored,
not engineered.

## 9. Commercialization gate (fail-closed)

No actionable signal offering may launch for a sport without: (1) that
sport's gate PASS recorded in this repository with reproducible fixtures,
and (2) explicit compliance approval recorded alongside it. Feature
authorization checks both flags and defaults to DENY. Cross-sport
aggregation never substitutes for a sport's own gate.

## 10. Required fixtures (evaluation harness)

The Phase 1.5 harness must ship deterministic fixtures proving each rule:

1. Push, void, DNP, cancellation → excluded from N with reason codes.
2. Missing or stale closing prices → unevaluable, reported not dropped.
3. Duplicate and correlated signals → deduplicated; correlation preserved
   in clustering; effective-n reported.
4. Source disagreement beyond tolerance → unevaluable.
5. Post-registration mutation attempts → rejected; append-only revision
   history with integrity hashes survives.
6. Model-version changes mid-evaluation → signals stratified by version;
   mixed-version confirmatory sets are unevaluable as registered.
7. Failing statistical gate and passing statistical gate → exact verdict
   outputs.
8. Insufficient effective information (few clusters) → inconclusive verdict
   path.
9. Second-attempt failure → mandatory retirement of the sport's
   commercial flag (authorization check denies).

## 11. Reproducibility

Every evaluation run records: input snapshot hashes, seed (20261024),
replicate count, cluster assignment table, the exact interval bounds, and
the decision. Re-running the recorded snapshot must reproduce the recorded
decision bit-for-bit.

## 12. Time-to-evidence arithmetic under the registered hurdle
(Addendum 2026-10-25, committed BEFORE any prospective observation was
collected — permitted by the header rule. No registered parameter in §§1–11
is modified by this section.)

**What the hurdle is.** The qualification hurdle `EDGE_HURDLE = 0.10`
(`src/freeze_slate.py:40`) is **10 percentage points of de-vig implied
probability**: a signal qualifies only when the model's probability for a
side exceeds the market's de-vig probability for the same side by ≥ 0.10
(and EV > 0, and the quote carries a receipt). It is deliberately
conservative: a calibrated model against a liquid book rarely shows gaps
above 3–5pp; 10pp gaps are dominated by data errors and thin books. That is
the intended behavior for a fail-closed qualification gate — and it has a
calendar consequence that must be stated, not discovered later.

**Expected qualification rate.** Under the 10pp hurdle, the expected share
of screened props that qualify is low single digits at best. The table below
converts an assumed qualification rate X into weeks-to-N, holding two
assumptions explicit: 200 NFL props screened per week (single sport), and
100% evaluability of qualifying signals (§3 exclusions — pushes, stale
closes, source disagreement — only make these numbers worse, proportionally
to 1/evaluable-rate).

| Qualification rate X | Qualified / week | Weeks to N=300 (descriptive look) | Weeks to N=600 (confirmatory) |
|---|---|---|---|
| 0.5% | 1 | 300 | 600 |
| 1% | 2 | 150 | 300 |
| 2% | 4 | 75 | 150 |
| 5% | 10 | 30 | 60 |

**Read this table before making calendar claims.** At X = 1%, the
confirmatory N=600 gate is ~300 weeks (~5.8 seasons) for NFL alone; any
roadmap assumption of a Q1 2027 verdict is not supported by this arithmetic
unless the observed qualification rate is materially above the low single
digits. Gates are per-sport and cross-sport pooling is forbidden (§1), so
other sports do not shorten NFL's calendar.

**Preregistered review point.** At the descriptive N=300 checkpoint (or
after 10,000 screened signals, whichever comes first), the observed
qualification rate is reported per §8. If it is below 1%, a hurdle
recalibration MAY be proposed as a protocol amendment under the header rule:
committed with rationale before further data collection, never applied
retroactively, and never applied to already-collected signals. The first
confirmatory attempt consumes an attempt regardless (§6).

**Related constant, no coupling (verified 2026-10-25).**
`src/grade_clv.py` defines its own `edge_hurdle: float = 0.10` (line 95) and
`--hurdle` default 0.10 (line 248). `grep -n '0\.10' src/grade_clv.py
src/freeze_slate.py` shows they are two independent literals — grade_clv
imports nothing from freeze_slate, so there is no shared constant to drift.
They are, however, the same quantity in the same units (the snapshot
evaluator's de-vig probability gap; `grade_clv.py:139` re-qualifies on
`edge >= edge_hurdle`), i.e. a duplicated threshold that must be tuned
together by hand. Tuning one without the other is a protocol amendment and
must be committed as such. (The external auditor's reading of `--hurdle` as
"CLV in points" was incorrect: CLV is aggregated separately and is not
gated by this parameter.)

## 13. Quote source on the close (effective 2026-11-16)

Scheduled odds after this date are Kalshi for NBA and Polymarket for NFL.
UFC has no registered quote source (§3), so this section does not give UFC a
provider. A closing row matches an entry only when `quote_source` is the same
tag, in addition to the same market, selection, line, and settlement rules. A
different tag is unmatched and is not a price comparison.

Tags are `odds_api:<book>`, `kalshi`, `polymarket`, or `unknown`. Rows already
in the ledger are tagged from the stored book name when that name identifies
one of those sources. Rows with no readable source stay `unknown`.

Headline CLV is computed inside one source class only. `odds_api`, `kalshi`,
`polymarket`, and `unknown` are not averaged together. The thresholds in §1
are unchanged. The operating parameters this section fills are in §3.

## 14. Close definition and per-source liquidity floors (effective [EFFECTIVE_DATE_SLOT])

This section is additive. It does not edit §13. The stored-field measurements
it relies on are in
[docs/research/close-quality-inventory-2026-11.md](research/close-quality-inventory-2026-11.md).
Every quantity below is a named slot. This section sets no numeric floor,
offset, notional, or date (§14.4).

### 14.1 Close definition per source

The close price is fixed per source. It is not chosen per row. The inventory
does not make "last trade" or "mid" unambiguous for either source, so both
price slots stay open.

| Source | Close price | What is stored today |
|---|---|---|
| Kalshi | [KALSHI_CLOSE_PRICE_SLOT] | American odds converted from the YES ask and the NO ask. Not a last trade. Not a mid. |
| Polymarket | [POLYMARKET_CLOSE_PRICE_SLOT] | American odds converted from each outcome token's best ask. Not a last trade. Not a mid. |

The cutoff is scheduled event start minus [CUTOFF_OFFSET_SLOT], for both
sources. The offset is fixed before any pull that uses it. A close is never
"the last available price." A capture inside the game, or in the window
between start and settlement, is not a close under this section.

A row captured at or after scheduled event start is unevaluable with reason
code `CLOSE_AFTER_START`. That code is distinct from `LINE_CHANGED`. A price
taken at or after scheduled start is an in-game or settlement-window price,
so it is not a pre-event close.

`CLOSE_AFTER_START` is the only reason code this section adds. A free-text
flag is not a category. Treatment of an unevaluable row stays the §3 rule:
excluded from N with that reason code, reported, never silently dropped, and
never counted as zero.

### 14.2 Quality floor

A close counts as confirmatory-grade only when it passes all three tests
below. Each test is a raw slot. There is no weighted composite and no index
that blends them.

1. **Spread at cutoff.** The implied-probability spread at the cutoff is at
   most [SPREAD_SLOT]. The value later written into that slot is bounded
   above by the same-source disagreement tolerance already registered in §13.
2. **Price impact at a fixed notional.** The price impact of lifting
   [NOTIONAL_SLOT] at the cutoff is at most [PRICE_IMPACT_SLOT].
3. **Close-window volume.** Traded volume in the close window is at least
   [VOLUME_SLOT].

Order-book depth at close is not recorded for Kalshi or for Polymarket. The
price-impact test cannot be computed from the rows stored today. Where the
current Polymarket feed stores a depth figure, it is top-of-book ask notional
at fetch time, not a full book, and the retained line series drops it. Kalshi
stores no depth. Those quantities are not one metric, and they are not
averaged into a shared liquidity score.

A row that fails any one test stays in the record and stays visible. It is
descriptive only: it does not count toward N = 600, it is not deleted, and it
is not recategorized.

### 14.3 Outside reference for the volume criterion

[REFERENCE_SLOT] is chosen by Nick and frozen before any pull that sets a
floor. The amendment that fills the slot names the reference, why that
reference, and the contract-mapping and settlement caveats. Kalshi contracts
and Polymarket contracts are not equivalent. This section does not choose a
reference.

### 14.4 Values

Every value in this section is a named slot. No floor, offset, notional,
spread, volume, accrual window, or effective date is filled here.

### 14.5 Two preregistered paths

Both paths are written here. Neither path is a license to fill a slot inside
this section.

**Fields-exist path.** When stored rows contain the field list in the
price-only paragraph below, floors are derived once from the historical pull
against the frozen reference. The derivation is published with the
distributions (quantiles and N per source) and a review date
[REVIEW_DATE_SLOT]. It is not refit per row.

**Price-only path.** The spread test is set from history when spread at the
cutoff is computable from stored prices. The volume test and the price-impact
test wait until prospective rows record a fixed field list for a minimum
accrual window [ACCRUAL_SLOT]. The list is: close-window traded volume, open
interest, bid, ask, spread at cutoff, last trade price, mid, cutoff
timestamp, and order-book depth at cutoff. The amendment that starts accrual
names the book scope per source (top of book, or full book). Depth from one
venue is not pooled with depth from the other. Floors are then set only by a
new anchored amendment. Rows recorded in the interim are descriptive only.

The linked inventory finds that field list absent from the stored rows, and
it finds spread at cutoff not stored for either source. The rows on hand
support the price-only path. They do not support executing the fields-exist
path.

### 14.6 No retroactive upgrade

Rows logged before [EFFECTIVE_DATE_SLOT] remain under §13. Nothing already
stored is re-scored, promoted, or re-tagged because this section exists. The
after-start captures in the inventory are a description of the files. They
are not a retroactive `CLOSE_AFTER_START` tag.

### 14.7 Scope

This section applies to Kalshi and Polymarket only. Legacy sportsbook rows
are unchanged. DFS break-evens are thresholds, not closes, and are out of
scope. UFC is unchanged from §3.

### 14.8 What a qualified close supports

CLV against a close that meets this section supports only the claim that the
entry was priced better than a preregistered qualified close. It does not
support a claim of edge. Calibration, model probability against outcomes, is
reported alongside that CLV and is not a substitute for it.

### 14.9 Edge-type tags

Any edge-type tagging scheme is written into this document before it is used.
A tag is assigned before the result is known and is immutable after
settlement. A row with no tag is descriptive only. This section does not
define a scheme.
