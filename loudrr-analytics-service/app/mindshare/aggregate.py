"""Gold: fold contributions into hourly buckets, sum into windowed mindshare, diff for movers.

  buckets   ms_contribution -> ms_bucket            # hourly (sector,entity) rollup [the scale trick]
  snapshot  ms_bucket -> ms_snapshot                # window = Σ last N buckets, normalized Σ=1/niche
  movers    last two ms_snapshot -> ms_mover        # gainers/losers/new/exit

Aggregation is done in Python (dialect-portable: SQLite dev, Postgres prod). Windows are rolling.
``snap_ts`` is one timestamp per run; hourly snapshots form the time-series the movers diff.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.session import Base, SessionLocal, engine
from app.mindshare.models import MsBucket, MsContribution, MsMover, MsSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ms.aggregate")

WINDOWS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}   # hours
# Recency half-life: a bucket's weight halves every N hours of age within the window. Calibrated
# vs Kaito (48h lifted top-10 overlap to 9/10) — recent attention counts more, like their momentum.
RECENCY_HALFLIFE_H = 48.0


def _naive(ts: datetime) -> datetime:
    """UTC-naive, for portable comparisons (SQLite returns naive, Postgres returns aware)."""
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hour(ts: datetime) -> datetime:
    return _naive(ts).replace(minute=0, second=0, microsecond=0)


async def rebuild_buckets(vertical: str = "crypto") -> int:
    """Recompute ms_bucket from ms_contribution for the vertical (v1: full rebuild, bounded)."""
    async with SessionLocal() as s:
        rows = (await s.execute(select(MsContribution).where(
            MsContribution.vertical == vertical))).scalars().all()
        agg: dict[tuple, list] = defaultdict(lambda: [0.0, 0])
        for r in rows:
            k = (r.sector, r.entity_id, _hour(r.ts))
            agg[k][0] += r.value
            agg[k][1] += 1
        await s.execute(delete(MsBucket).where(MsBucket.vertical == vertical))
        s.add_all([
            MsBucket(vertical=vertical, sector=sec, entity_id=eid, hour_ts=hr,
                     weighted_value=val, mention_count=cnt)
            for (sec, eid, hr), (val, cnt) in agg.items()
        ])
        await s.commit()
        return len(agg)


async def snapshot(vertical: str = "crypto", *, now: datetime | None = None) -> dict:
    now = _naive(now) if now else _utcnow()
    async with SessionLocal() as s:
        buckets = (await s.execute(select(MsBucket).where(
            MsBucket.vertical == vertical))).scalars().all()
        # previous snapshot mindshare per (sector,window,entity) for deltas
        prev_ts = (await s.execute(
            select(MsSnapshot.snap_ts).where(MsSnapshot.vertical == vertical)
            .order_by(MsSnapshot.snap_ts.desc()).limit(1))).scalar()
        prev = {}
        if prev_ts:
            for r in (await s.execute(select(MsSnapshot).where(
                    MsSnapshot.vertical == vertical, MsSnapshot.snap_ts == prev_ts))).scalars().all():
                prev[(r.sector, r.window, r.entity_id)] = r.mindshare

        out, written = {}, []
        for window, hours in WINDOWS.items():
            cutoff = now - timedelta(hours=hours)
            totals: dict[str, float] = defaultdict(float)        # sector -> total weighted
            per: dict[tuple, float] = defaultdict(float)         # (sector,entity) -> weighted
            for b in buckets:
                hb = _naive(b.hour_ts)
                if hb >= cutoff:
                    age_h = (now - hb).total_seconds() / 3600.0
                    decay = 0.5 ** (age_h / RECENCY_HALFLIFE_H)   # recent buckets weigh more
                    val = b.weighted_value * decay
                    per[(b.sector, b.entity_id)] += val
                    totals[b.sector] += val
            # rank + normalize per sector
            by_sector: dict[str, list] = defaultdict(list)
            for (sector, eid), val in per.items():
                by_sector[sector].append((eid, val))
            n = 0
            for sector, items in by_sector.items():
                tot = totals[sector] or 1.0
                items.sort(key=lambda x: x[1], reverse=True)
                for rank, (eid, val) in enumerate(items, 1):
                    ms = val / tot
                    delta = ms - prev.get((sector, window, eid), 0.0) if prev_ts else None
                    written.append(MsSnapshot(
                        snap_ts=now, vertical=vertical, sector=sector, window=window,
                        entity_id=eid, rank=rank, mindshare=ms, mindshare_delta=delta))
                    n += 1
            out[window] = n
        s.add_all(written)
        await s.commit()
        return {"vertical": vertical, "snap_ts": now.isoformat(), "rows": len(written), "by_window": out}


async def compute_movers(vertical: str = "crypto") -> dict:
    async with SessionLocal() as s:
        ts2 = (await s.execute(
            select(MsSnapshot.snap_ts).where(MsSnapshot.vertical == vertical)
            .distinct().order_by(MsSnapshot.snap_ts.desc()).limit(2))).scalars().all()
        if not ts2:
            return {"movers": 0, "note": "no snapshots"}
        cur_ts = ts2[0]
        prev_ts = ts2[1] if len(ts2) > 1 else None
        cur_rows = (await s.execute(select(MsSnapshot).where(
            MsSnapshot.vertical == vertical, MsSnapshot.snap_ts == cur_ts))).scalars().all()
        prev_rows = [] if prev_ts is None else (await s.execute(select(MsSnapshot).where(
            MsSnapshot.vertical == vertical, MsSnapshot.snap_ts == prev_ts))).scalars().all()
        prev = {(r.sector, r.window, r.entity_id): r for r in prev_rows}
        cur = {(r.sector, r.window, r.entity_id): r for r in cur_rows}

        await s.execute(delete(MsMover).where(MsMover.vertical == vertical,
                                              MsMover.snap_ts == cur_ts))
        movers = []
        for key, r in cur.items():
            p = prev.get(key)
            if p is None:
                kind, d, d_rank = "new", r.mindshare, None
            else:
                d = r.mindshare - p.mindshare
                d_rank = p.rank - r.rank
                kind = "gain" if d >= 0 else "lose"
            movers.append(MsMover(snap_ts=cur_ts, vertical=vertical, sector=r.sector,
                                  window=r.window, entity_id=r.entity_id, d_mindshare=d,
                                  d_rank=d_rank, kind=kind))
        for key, p in prev.items():
            if key not in cur:
                movers.append(MsMover(snap_ts=cur_ts, vertical=vertical, sector=p.sector,
                                      window=p.window, entity_id=p.entity_id,
                                      d_mindshare=-p.mindshare, d_rank=None, kind="exit"))
        s.add_all(movers)
        await s.commit()
        return {"vertical": vertical, "movers": len(movers),
                "compared": [cur_ts.isoformat(), prev_ts.isoformat() if prev_ts else None]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="crypto")
    args = ap.parse_args()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    nb = await rebuild_buckets(args.vertical)
    snap = await snapshot(args.vertical)
    mv = await compute_movers(args.vertical)
    logger.info("buckets=%s snapshot=%s movers=%s", nb, snap, mv)
    print({"buckets": nb, "snapshot": snap, "movers": mv})


if __name__ == "__main__":
    asyncio.run(main())
