"""Resilient non-destructive backup of the Postgres crawl DB -> gzipped CSV per table + manifest.
Big `edges` table exported in small member-batches with auto-reconnect+retry on dropped connections
(the remote public-port proxy resets large transfers). Read-only on the DB. Reads LOUDRR_PG_PASSWORD."""
import asyncio, asyncpg, gzip, os, json, csv, datetime
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
BATCH = 20  # members per edges query (small => short transfer, survives proxy)
DROP_ERRORS = (asyncpg.exceptions.ConnectionDoesNotExistError,
               asyncpg.exceptions.InterfaceError, ConnectionResetError, OSError, asyncio.TimeoutError)


async def connect():
    return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                 password=PW, database="loudrr_analytics", timeout=120, command_timeout=120)


async def fetch_retry(h, query, *args, tries=8):
    for a in range(tries):
        try:
            return await h["c"].fetch(query, *args)
        except DROP_ERRORS:
            print(f"   conn dropped, reconnecting ({a+1}/{tries})...", flush=True)
            try:
                await h["c"].close()
            except Exception:
                pass
            await asyncio.sleep(2 + a)
            h["c"] = await connect()
    raise RuntimeError("batch failed after retries")


async def dump_small(h, t, path):
    rows = await fetch_retry(h, f'SELECT * FROM "{t}"')
    cols = list(rows[0].keys()) if rows else [r["column_name"] for r in await fetch_retry(
        h, "SELECT column_name FROM information_schema.columns WHERE table_name=$1 ORDER BY ordinal_position", t)]
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    return len(rows)


async def dump_edges(h, path):
    members = [r["user_id"] for r in await fetch_retry(h, "SELECT user_id FROM smart_set")]
    total = 0
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["follower_id", "followee_id", "seen_at"])
        for i in range(0, len(members), BATCH):
            rows = await fetch_retry(
                h, "SELECT follower_id, followee_id, seen_at FROM edges WHERE follower_id = ANY($1::text[])",
                members[i:i + BATCH])
            for r in rows:
                w.writerow([r["follower_id"], r["followee_id"], r["seen_at"]])
            f.flush()
            total += len(rows)
            if (i // BATCH) % 10 == 0 or i + BATCH >= len(members):
                print(f"  edges: {min(i+BATCH,len(members))}/{len(members)} members  {total:,} rows", flush=True)
    return total


async def main():
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = f"data/backups/{stamp}"
    os.makedirs(outdir, exist_ok=True)
    h = {"c": await connect()}
    # ONLY our crawl data — skip the 134 NocoDB/leftover tables polluting this DB
    OURS = ("edges", "smart_set", "crawl_runs", "crawl_meta", "score_snapshots")
    present = {r["tablename"] for r in await fetch_retry(
        h, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    tables = [t for t in OURS if t in present]
    tables.sort(key=lambda t: (t != "edges", t))  # edges first (biggest, riskiest)
    manifest = {"stamp": stamp, "tables": {}}
    print(f"backing up {len(tables)} tables -> {outdir}", flush=True)
    for t in tables:
        path = f"{outdir}/{t}.csv.gz"
        n = await (dump_edges(h, path) if t == "edges" else dump_small(h, t, path))
        size = os.path.getsize(path)
        manifest["tables"][t] = {"rows": n, "bytes": size, "file": f"{t}.csv.gz"}
        print(f"  DONE {t:<22} {n:>12,} rows -> {size/1e6:>7.1f} MB", flush=True)
        json.dump(manifest, open(f"{outdir}/manifest.json", "w"), indent=2)  # checkpoint after each table
    await h["c"].close()
    print(f"\nBACKUP COMPLETE -> {outdir}", flush=True)


asyncio.run(main())
