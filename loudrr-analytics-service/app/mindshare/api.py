"""Read-only serving for the mindshare engine — cheap reads off the pre-aggregated gold tables.

Mounted on the main FastAPI app under ``/v1/mindshare`` (see app/main.py). Everything here is a
single indexed read of the latest ``ms_snapshot`` / ``ms_mover`` for a niche — no compute in the
request path (that's the whole point of the hourly snapshots).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.mindshare.models import MsMover, MsRoster, MsSnapshot

router = APIRouter(tags=["mindshare"])
WINDOWS = ("1h", "24h", "7d", "30d")


@router.get("")
async def index(s: AsyncSession = Depends(get_session)) -> dict:
    """Discoverability: which (vertical, sector) leaderboards exist + the latest snapshot time."""
    snap_ts = (await s.execute(select(MsSnapshot.snap_ts)
                               .order_by(MsSnapshot.snap_ts.desc()).limit(1))).scalar()
    slices = (await s.execute(select(MsSnapshot.vertical, MsSnapshot.sector).distinct())).all()
    by_vertical: dict[str, list[str]] = {}
    for v, sec in slices:
        by_vertical.setdefault(v, [])
        if sec not in by_vertical[v]:
            by_vertical[v].append(sec)
    return {
        "service": "loudrr-mindshare",
        "updated": snap_ts.isoformat() if snap_ts else None,
        "windows": list(WINDOWS),
        "verticals": by_vertical,
        "endpoints": {
            "leaderboard": "/v1/mindshare/{vertical}/{sector}?window=7d&top=50",
            "movers": "/v1/mindshare/{vertical}/{sector}/movers?window=7d&kind=gain",
        },
    }


async def _token_meta(s: AsyncSession, vertical: str) -> dict[str, dict]:
    rows = (await s.execute(select(MsRoster).where(
        MsRoster.vertical == vertical, MsRoster.role == "token"))).scalars().all()
    return {r.entity_id: {"symbol": r.symbol, "name": r.name} for r in rows}


@router.get("/{vertical}/{sector}")
async def leaderboard(
    vertical: str, sector: str,
    window: str = Query("7d", enum=list(WINDOWS)),
    top: int = Query(50, ge=1, le=500),
    s: AsyncSession = Depends(get_session),
) -> dict:
    """Loudrr mindshare leaderboard for a niche (latest hourly snapshot)."""
    snap_ts = (await s.execute(select(MsSnapshot.snap_ts).where(
        MsSnapshot.vertical == vertical).order_by(MsSnapshot.snap_ts.desc()).limit(1))).scalar()
    if not snap_ts:
        return {"vertical": vertical, "sector": sector, "window": window, "items": []}
    rows = (await s.execute(select(MsSnapshot).where(
        MsSnapshot.vertical == vertical, MsSnapshot.sector == sector,
        MsSnapshot.window == window, MsSnapshot.snap_ts == snap_ts)
        .order_by(MsSnapshot.rank).limit(top))).scalars().all()
    meta = await _token_meta(s, vertical)
    return {
        "vertical": vertical, "sector": sector, "window": window, "snapshot": snap_ts.isoformat(),
        "items": [{
            "rank": r.rank, "entity_id": r.entity_id,
            "symbol": (meta.get(r.entity_id) or {}).get("symbol") or r.entity_id,
            "name": (meta.get(r.entity_id) or {}).get("name"),
            "mindshare": round(r.mindshare, 6),
            "mindshare_delta": round(r.mindshare_delta, 6) if r.mindshare_delta is not None else None,
        } for r in rows],
    }


@router.get("/{vertical}/{sector}/movers")
async def movers(
    vertical: str, sector: str,
    window: str = Query("7d", enum=list(WINDOWS)),
    kind: str = Query("all", enum=["all", "gain", "lose", "new", "exit"]),
    top: int = Query(20, ge=1, le=200),
    s: AsyncSession = Depends(get_session),
) -> dict:
    """Gainers / losers for a niche between the two latest snapshots."""
    snap_ts = (await s.execute(select(MsMover.snap_ts).where(
        MsMover.vertical == vertical).order_by(MsMover.snap_ts.desc()).limit(1))).scalar()
    if not snap_ts:
        return {"vertical": vertical, "sector": sector, "window": window, "movers": []}
    q = select(MsMover).where(MsMover.vertical == vertical, MsMover.sector == sector,
                              MsMover.window == window, MsMover.snap_ts == snap_ts)
    if kind != "all":
        q = q.where(MsMover.kind == kind)
    rows = (await s.execute(q)).scalars().all()
    rows.sort(key=lambda r: r.d_mindshare, reverse=True)
    if kind == "all":
        rows = rows[:top] + rows[-top:] if len(rows) > 2 * top else rows
    else:
        rows = rows[:top] if kind in ("gain", "new") else list(reversed(rows))[:top]
    meta = await _token_meta(s, vertical)
    return {
        "vertical": vertical, "sector": sector, "window": window, "snapshot": snap_ts.isoformat(),
        "movers": [{
            "entity_id": r.entity_id,
            "symbol": (meta.get(r.entity_id) or {}).get("symbol") or r.entity_id,
            "name": (meta.get(r.entity_id) or {}).get("name"),
            "d_mindshare": round(r.d_mindshare, 6), "d_rank": r.d_rank, "kind": r.kind,
        } for r in rows],
    }
