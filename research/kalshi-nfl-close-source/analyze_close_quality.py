"""Apply the EDGE_GATE §14 options-sheet checks to close-quality captures.

Checks (per close = last pre-start capture at or before scheduled start - 5 min,
fresh only if within 15 min before that cutoff):
  spread      ask - bid <= 0.05
  impact      buying $100 from the best ask through <=5 captured levels moves the
              average fill <= 0.01 above the best ask (unfilled = fail)
  ask_sum     YES ask + NO ask <= 1.15 (NO ask = 1 - YES bid when not captured)
A market "survives" when it has a fresh T-5 close that passes spread and impact.
Usage: analyze_close_quality.py FILE [FILE...] [--source S] [--filter-props]
"""
from __future__ import annotations
import argparse, json, statistics
from collections import defaultdict
from datetime import datetime, timedelta

PROP_SERIES = {  # single-game player props comparable to the Polymarket NFL feed
    "KXNFLPASSYDS", "KXNFLRSHYDS", "KXNFLRECYDS", "KXNFLREC", "KXNFLANYTD",
    "KXNFLPASSTDS", "KXNFLLADDERRECYDS", "KXNFLLADDERRSHYDS", "KXNFLLADDERPASSYDS",
    "KXNFLRSHYDSPRECISE", "KXNFLRECYDSPRECISE", "KXNFLPASSYDSPRECISE", "KXNFLFIRSTTD",
    "KXNFLFFPTS", "KXNFLINT", "KXNFLSACK", "KXNFLRUSHYDS", "KXNFLRECEPTIONS",
}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def impact(asks, notional=100.0):
    if not asks:
        return None
    best = asks[0]["price"]
    left, cost, qty = notional, 0.0, 0.0
    for lv in asks:
        p, sz = lv["price"], lv["size"]
        if p <= 0:
            continue
        take = min(sz, left / p)
        cost += take * p; qty += take; left -= take * p
        if left <= 1e-9:
            return cost / qty - best
    return None  # not filled inside captured levels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--source")
    ap.add_argument("--filter-props", action="store_true")
    ap.add_argument("--phase")
    ap.add_argument("--cutoff", type=float, default=5.0)
    ap.add_argument("--json-out")
    ap.add_argument("--main-lines", action="store_true",
                    help="keep one market per player and stat: the rung whose T-5 mid is nearest 50%%")
    a = ap.parse_args()
    recs = []
    for f in a.files:
        for line in open(f):
            if line.strip():
                r = json.loads(line)
                if r.get("schema_version") != 2 or not r.get("pre_start"):
                    continue
                if a.source and r.get("source") != a.source:
                    continue
                if a.phase and r.get("phase") != a.phase:
                    continue
                if a.filter_props and r.get("series_ticker") not in PROP_SERIES:
                    continue
                recs.append(r)
    by_mkt = defaultdict(list)
    for r in recs:
        by_mkt[r["market_id"]].append(r)
    games = {r["game_id"] for r in recs}
    closes, fresh_markets = [], 0
    for mid, rs in by_mkt.items():
        start = ts(rs[0]["scheduled_start"])
        cut = start - timedelta(minutes=a.cutoff)
        pre = [r for r in rs if ts(r["captured_at"]) <= cut]
        if not pre:
            continue
        c = max(pre, key=lambda r: r["captured_at"])
        if cut - ts(c["captured_at"]) > timedelta(minutes=15):
            continue
        fresh_markets += 1
        closes.append(c)

    if a.main_lines:
        best = {}
        for c in closes:
            if c.get("bid") is None or c.get("ask") is None:
                continue
            player = str(c.get("market_title") or "").split(":")[0]
            key = (c.get("event_ticker"), c.get("series_ticker"), player)
            d = abs((c["bid"] + c["ask"]) / 2 - 0.5)
            if key not in best or d < best[key][0]:
                best[key] = (d, c)
        closes = [c for _, c in best.values()]
        keep = {c["market_id"] for c in closes}
        by_mkt = {k: v for k, v in by_mkt.items() if k in keep}

    def metrics(c):
        bid, ask = c.get("bid"), c.get("ask")
        spread = (ask - bid) if (bid is not None and ask is not None and bid > 0 and ask > 0) else None
        asks = ((c.get("depth_levels") or {}).get("asks")) or []
        imp = impact(asks)
        no_ask = c.get("no_ask")
        no_ask = float(no_ask) if no_ask not in (None, "") else ((1 - bid) if bid else None)
        s = (ask + no_ask) if (ask and no_ask) else None
        return spread, imp, s

    out = {"captures": len(recs), "games": len(games), "markets": len(by_mkt),
           "markets_with_fresh_close": len(closes)}
    sp, ok_sp, ok_imp, ok_both, ok_sum, n = [], 0, 0, 0, 0, len(closes)
    allsp = [m for m in (metrics(r)[0] for r in recs) if m is not None]
    for c in closes:
        spread, imp, s = metrics(c)
        a1 = spread is not None and spread <= 0.05 + 1e-9
        a2 = imp is not None and imp <= 0.01 + 1e-9
        ok_sp += a1; ok_imp += a2; ok_both += a1 and a2
        ok_sum += s is not None and s <= 1.15 + 1e-9
        if spread is not None:
            sp.append(spread)
    pct = lambda x, d: round(100 * x / d, 1) if d else None
    out.update({
        "all_captures_median_spread_pp": round(100 * statistics.median(allsp), 1) if allsp else None,
        "all_captures_spread_le_5pp_pct": pct(sum(s <= 0.05 + 1e-9 for s in allsp), len(allsp)),
        "t5_closes": n,
        "t5_close_median_spread_pp": round(100 * statistics.median(sp), 1) if sp else None,
        "t5_spread_le_5pp_pct_of_closes": pct(ok_sp, n),
        "t5_impact100_le_1pp_pct_of_closes": pct(ok_imp, n),
        "t5_both_floors_pct_of_closes": pct(ok_both, n),
        "t5_both_floors_pct_of_markets": pct(ok_both, len(by_mkt)),
        "t5_survivors": ok_both,
        "t5_ask_sum_le_115_pct_of_closes": pct(ok_sum, n),
        "t5_ask_sum_le_115_pct_of_markets": pct(ok_sum, len(by_mkt)),
    })
    print(json.dumps(out, indent=1))
    if a.json_out:
        open(a.json_out, "w").write(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
