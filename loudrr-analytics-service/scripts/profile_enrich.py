"""Profile-enrich: fetch followers/following/bio for voters + top discovered, flag dead/bots/crypto.
Pure Twitter-API (twitterapi.io) -> local CSV. Touches NO database, safe to run alongside the backup."""
import asyncio, csv, os, re
import httpx
from dotenv import load_dotenv
load_dotenv()

KEY = os.getenv("TWITTERAPI_IO_KEY", "")
OUT = "data/exports/profiles_enriched.csv"
CRYPTO = re.compile(r"(crypto|web3|defi|nft|bitcoin|\bbtc\b|\beth\b|ethereum|solana|\bsol\b|blockchain|"
                    r"\bdao\b|token|onchain|on-chain|degen|memecoin|\$[a-z]{2,6}\b|wagmi|hodl|altcoin|"
                    r"validator|staking|airdrop|tokenomics|l2|rollup|zk|trading|trader|markets)", re.I)


def load_targets():
    """Return {user_id: source} from the two CSVs (voters + discovered)."""
    t = {}
    for path, src in [("data/exports/smart_set_voters.csv", "voter"),
                      ("data/exports/discovered_top_ids.csv", "discovered")]:
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path, encoding="utf-8")):
            uid = r.get("user_id")
            if uid and uid not in t:
                t[uid] = src
    return t


async def resolve(ids):
    out = {}
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(base_url="https://api.twitterapi.io", timeout=40,
                                 headers={"x-api-key": KEY, "User-Agent": "Mozilla/5.0"}) as cl:
        async def one(i, batch):
            async with sem:
                for attempt in range(4):
                    try:
                        r = await cl.get("/twitter/user/batch_info_by_ids",
                                         params={"userIds": ",".join(batch)})
                        data = r.json().get("data") or r.json().get("users") or []
                        for u in data:
                            uid = str(u.get("id") or u.get("userId") or "")
                            if uid:
                                out[uid] = u
                        return
                    except Exception:
                        await asyncio.sleep(1.5 * (attempt + 1))
            if i % 50 == 0:
                print(f"  ...batch {i}", flush=True)
        batches = [ids[k:k + 100] for k in range(0, len(ids), 100)]
        print(f"fetching {len(ids):,} profiles in {len(batches)} batches...", flush=True)
        await asyncio.gather(*(one(i, b) for i, b in enumerate(batches)))
    return out


async def main():
    targets = load_targets()
    ids = list(targets)
    info = await resolve(ids)
    print(f"resolved {len(info):,}/{len(ids):,}", flush=True)
    dead = bot = crypto = 0
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "name", "followers", "following", "blue_verified",
                    "dead", "bot_suspect", "crypto_flag", "source", "description"])
        for uid in ids:
            u = info.get(uid)
            if not u:
                dead += 1
                w.writerow([uid, "", "", "", "", "", 1, "", "", targets[uid], ""])
                continue
            fol = u.get("followers") or u.get("followers_count") or 0
            flw = u.get("following") or u.get("following_count") or 0
            desc = (u.get("description") or "").replace("\n", " ").strip()
            bs = 1 if (flw and int(flw) > 50000) else 0
            cf = 1 if CRYPTO.search(desc) else 0
            bot += bs; crypto += cf
            w.writerow([uid, u.get("userName") or "", (u.get("name") or "").replace("\n", " "),
                        fol, flw, 1 if u.get("isBlueVerified") else 0, 0, bs, cf, targets[uid], desc[:160]])
    print(f"\nwrote {OUT}", flush=True)
    print(f"  dead/suspended : {dead:,}", flush=True)
    print(f"  bot_suspect (>50k following): {bot:,}", flush=True)
    print(f"  crypto bio flag: {crypto:,}", flush=True)


asyncio.run(main())
