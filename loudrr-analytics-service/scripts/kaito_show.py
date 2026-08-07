"""Pretty-print ONE Kaito leaderboard slice from the DB (the table stacks ~135 of them).

    python -m scripts.kaito_show                                  # voices/crypto/ALL/7d (default)
    python -m scripts.kaito_show --kind companies --sector EXCHANGE
    python -m scripts.kaito_show --vertical ai --kind voices --duration 30d --top 20
    python -m scripts.kaito_show --list                           # show every available slice

The kaito_mindshare table holds one row per (kind, vertical, sector, duration, account). You MUST
filter to a single slice to see a clean rank 1..N ladder — otherwise every niche's #1 piles up.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

DB = "data/harvest.db"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="voices", choices=["voices", "companies"])
    ap.add_argument("--vertical", default="crypto")
    ap.add_argument("--sector", default="ALL")
    ap.add_argument("--duration", default="7d")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--run", default=None, help="run_id (default: latest)")
    ap.add_argument("--list", action="store_true", help="list all available slices and exit")
    args = ap.parse_args()

    c = sqlite3.connect(DB)
    run = args.run or c.execute("select max(run_id) from kaito_mindshare").fetchone()[0]
    if not run:
        sys.exit("no kaito_mindshare data yet — run scripts.scrape_kaito first")

    if args.list:
        print(f"slices in run {run} (kind/vertical/sector/duration -> rows):")
        for r in c.execute(
            "select kind,vertical,sector,duration,count(*) from kaito_mindshare "
            "where run_id=? group by kind,vertical,sector,duration order by kind,vertical,sector,duration",
            (run,),
        ):
            print(f"  {r[0]:9} {r[1]:7} {r[2]:18} {r[3]:4} -> {r[4]}")
        return

    rows = c.execute(
        "select rank, handle, symbol, name, mindshare, mindshare_delta, entity_id "
        "from kaito_mindshare where run_id=? and kind=? and vertical=? and sector=? and duration=? "
        "order by rank limit ?",
        (run, args.kind, args.vertical, args.sector, args.duration, args.top),
    ).fetchall()
    if not rows:
        sys.exit(f"no rows for {args.kind}/{args.vertical}/{args.sector}/{args.duration} "
                 f"— try --list to see available slices")

    label = "@handle" if args.kind == "voices" else "ticker"
    print(f"\nKaito {args.kind} · {args.vertical} · sector={args.sector} · {args.duration}  (run {run})")
    print(f"{'#':>3}  {label:24} {'mindshare':>10} {'Δ':>10}  name")
    print("-" * 78)
    for rank, handle, symbol, name, ms, delta, eid in rows:
        ident = ("@" + handle) if (args.kind == "voices" and handle) else (symbol or eid or "")
        ms_s = f"{ms*100:.2f}%" if ms is not None else "-"
        d_s = (f"{delta*100:+.2f}%" if delta is not None else "-")
        print(f"{rank:>3}  {ident:24} {ms_s:>10} {d_s:>10}  {(name or '')[:28]}")
    print()


if __name__ == "__main__":
    main()
