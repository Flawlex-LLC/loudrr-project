"""Resolve top-discovered user_ids -> @handles, attach vendor scores, write readable CSV."""
import asyncio, csv, os, sqlite3
import httpx
from dotenv import load_dotenv
load_dotenv()

KEY = os.getenv("TWITTERAPI_IO_KEY", "")
SRC = "data/exports/discovered_top_ids.csv"
OUT = "data/exports/discovered_top_named.csv"


def load_vendor():
    cur = sqlite3.connect("data/harvest.db").cursor()
    sor = {u.lower(): s for u, s in cur.execute(
        "SELECT username, sorsa_score FROM harvested_scores WHERE username IS NOT NULL AND sorsa_score IS NOT NULL")}
    ts = {u.lower(): s for u, s in cur.execute(
        "SELECT username, twitterscore FROM twitterscore_accounts WHERE username IS NOT NULL AND twitterscore IS NOT NULL")}
    return sor, ts


async def resolve(ids):
    """Return {user_id: (username, name, followers)} via batch_info_by_ids (100/call)."""
    out = {}
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(base_url="https://api.twitterapi.io", timeout=40,
                                 headers={"x-api-key": KEY, "User-Agent": "Mozilla/5.0"}) as cl:
        async def one(batch):
            async with sem:
                for attempt in range(4):
                    try:
                        r = await cl.get("/twitter/user/batch_info_by_ids",
                                         params={"userIds": ",".join(batch)})
                        data = r.json().get("data") or r.json().get("users") or []
                        for u in data:
                            uid = str(u.get("id") or u.get("userId") or "")
                            if uid:
                                out[uid] = (u.get("userName") or u.get("screen_name") or "",
                                            u.get("name") or "", u.get("followers") or u.get("followers_count") or 0)
                        return
                    except Exception:
                        await asyncio.sleep(1.5 * (attempt + 1))
        batches = [ids[i:i + 100] for i in range(0, len(ids), 100)]
        await asyncio.gather(*(one(b) for b in batches))
    return out


async def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
    ids = [r["user_id"] for r in rows]
    print(f"resolving {len(ids):,} ids in {len(ids)//100+1} batches...")
    names = await resolve(ids)
    print(f"resolved {len(names):,}/{len(ids):,}")
    sor, ts = load_vendor()
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "username", "name", "our_score", "our_score_div5",
                    "elite_followers", "x_followers", "sorsa", "twitterscore"])
        rank = 0
        for r in rows:
            uid = r["user_id"]
            un, nm, fol = names.get(uid, ("", "", ""))
            rank += 1
            unl = (un or "").lower()
            w.writerow([rank, un, nm, r["our_score"], r["our_score_div5"],
                        r["elite_followers"], fol,
                        round(sor[unl], 1) if unl in sor else "",
                        round(ts[unl], 1) if unl in ts else ""])
    print(f"wrote {OUT} ({len(rows)} rows)")


asyncio.run(main())
