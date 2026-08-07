"""Sweep Kaito's mindshare arena into our DB — fast HTTP + rotating proxies.

    python -m scripts.scrape_kaito                      # crypto, companies+voices, all sectors/durations
    python -m scripts.scrape_kaito --vertical all       # crypto + ai + trading
    python -m scripts.scrape_kaito --kind companies --durations 7d --sectors ALL
    python -m scripts.scrape_kaito --dump-only          # scrape to JSON files, skip the DB
    python -m scripts.scrape_kaito --from-dump data/kaito/<run_id>   # re-load JSON into DB, no scrape
    python -m scripts.scrape_kaito --browser            # use the Camoufox/Cloudflare fallback

Default path is ``KaitoHTTPClient`` (curl_cffi + Webshare rotation) — the ``/voices/*`` API isn't
Cloudflare-gated, so no browser is needed and calls run concurrently. One run = one ``run_id``
(UTC). Leaderboards parse into ``kaito_mindshare`` (queryable time-series); every payload is
archived into ``kaito_captures`` + dumped to ``data/kaito/<run_id>/`` with a ``manifest.json``.
See docs/kaito_reverse_engineering.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.clients.kaito import (
    DURATIONS, SECTORS, VERTICALS, _SECTOR_DURATION_EPS, KaitoClient, KaitoHTTPClient,
)
from app.db.models import KaitoCapture, KaitoMindshare
from app.db.session import Base, SessionLocal, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scrape_kaito")

# Endpoints fetched per slice. sector_leaderboard is parsed into rows; the rest are archived raw.
SLICE_ENDPOINTS = ["sector_leaderboard", "mindshare_heatmap", "mindshare_delta_all",
                   "mindshare_ratio_all", "followers_change", "mindshare_language"]
DATA_DIR = Path("data/kaito")
# Known unsupported (endpoint, duration) combos — skip to keep the sweep clean (verified 400s).
ENDPOINT_BAD_DURATIONS = {"followers_change": {"12m"}}


@dataclass
class Slice:
    kind: str
    vertical: str
    sector: str
    duration: str
    endpoint: str
    url: str
    data: object


def _row_to_mindshare(s: Slice, run_id: str, rank_fallback: int, row: dict) -> KaitoMindshare:
    """Map one leaderboard row (company OR kol shape) to a KaitoMindshare record."""
    entity_type = "company" if s.kind == "companies" else "kol"
    entity_id = (row.get("company_id") or row.get("user_id") or row.get("ticker_id")
                 or row.get("ticker") or row.get("id"))
    return KaitoMindshare(
        run_id=run_id, kind=s.kind, vertical=s.vertical, sector=s.sector, duration=s.duration,
        rank=row.get("rank", rank_fallback),
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        handle=row.get("username") or row.get("screen_name") or row.get("twitter_username"),
        symbol=row.get("symbol") or row.get("display_ticker") or row.get("ticker"),
        name=row.get("name") or row.get("fullname"),
        logo=row.get("logo") or row.get("avatar") or row.get("icon"),
        mindshare=row.get("mindshare"),
        mindshare_prev=row.get("snapshot_last_7d_mindshare") or row.get("current"),
        mindshare_delta=row.get("mindshare_delta") or row.get("change_7d"),
        followers=row.get("followers") or row.get("followers_count"),
        smart_followers=row.get("smart_followers"),
        raw=row,
    )


def _build(slices: list[Slice], run_id: str) -> tuple[list, list, int]:
    captures, rows, n_rows = [], [], 0
    for s in slices:
        captures.append(KaitoCapture(
            run_id=run_id, kind=s.kind, vertical=s.vertical, sector=s.sector,
            duration=s.duration, endpoint=s.endpoint, url=s.url, http_status=200, payload=s.data))
        if s.endpoint == "sector_leaderboard" and isinstance(s.data, list):
            for i, row in enumerate(s.data, 1):
                if isinstance(row, dict):
                    rows.append(_row_to_mindshare(s, run_id, i, row))
            n_rows += len(s.data)
    return captures, rows, n_rows


def _jobs(kinds: list[str], verticals: list[str], durations: list[str],
          sectors_override: list[str] | None) -> list[tuple]:
    """Build the (kind,vertical,sector,duration,endpoint) matrix. Duration-varying endpoints
    fan over durations; the *_all endpoints are fetched once per (kind,vertical,sector)."""
    jobs = []
    for kind in kinds:
        for vertical in verticals:
            sectors = sectors_override or SECTORS.get((kind, vertical), ["ALL"])
            for sector in sectors:
                for endpoint in SLICE_ENDPOINTS:
                    if endpoint in _SECTOR_DURATION_EPS:
                        bad = ENDPOINT_BAD_DURATIONS.get(endpoint, set())
                        for d in durations:
                            if d not in bad:
                                jobs.append((kind, vertical, sector, d, endpoint))
                    else:
                        jobs.append((kind, vertical, sector, "all", endpoint))
    return jobs


async def _scrape(args, run_id: str, out_dir: Path) -> list[Slice]:
    kinds = ["companies", "voices"] if args.kind == "both" else [args.kind]
    verticals = VERTICALS if args.vertical == "all" else [args.vertical]
    jobs = _jobs(kinds, verticals, args.durations, args.sectors)
    client = (KaitoClient(headless=args.headless or None) if args.browser
              else KaitoHTTPClient(proxy_file=args.proxy_file, concurrency=args.concurrency))
    slices: list[Slice] = []
    manifest: list[dict] = []
    lock = asyncio.Lock()

    async with client as kc:
        logger.info("run_id=%s — sweeping %d jobs via %s", run_id, len(jobs), type(kc).__name__)

        async def do(job):
            kind, vertical, sector, duration, endpoint = job
            extra = {"limit": args.limit} if endpoint == "sector_leaderboard" else {}
            try:
                data = await kc.fetch(kind, vertical, endpoint, sector=sector, duration=duration, **extra)
            except Exception as e:  # noqa: BLE001 - keep sweeping; log the slice
                logger.warning("err %s/%s/%s/%s/%s: %s", kind, vertical, sector, duration, endpoint, e)
                return
            url = kc._endpoint_path(kind, vertical, endpoint)
            fn = f"{kind}_{vertical}_{sector}_{duration}_{endpoint}.json"
            (out_dir / fn).write_text(json.dumps(data), encoding="utf-8")
            async with lock:
                manifest.append({"file": fn, "kind": kind, "vertical": vertical, "sector": sector,
                                 "duration": duration, "endpoint": endpoint, "url": url})
                slices.append(Slice(kind, vertical, sector, duration, endpoint, url, data))

        await asyncio.gather(*(do(j) for j in jobs))

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return slices


def _load_dump(dump_dir: Path) -> list[Slice]:
    """Reconstruct slices from a dump dir (uses manifest.json if present, else filenames)."""
    man = dump_dir / "manifest.json"
    slices = []
    if man.exists():
        for m in json.loads(man.read_text(encoding="utf-8")):
            data = json.loads((dump_dir / m["file"]).read_text(encoding="utf-8"))
            slices.append(Slice(m["kind"], m["vertical"], m["sector"], m["duration"],
                                m["endpoint"], m["url"], data))
        return slices
    durs = DURATIONS + ["all"]
    for f in sorted(dump_dir.glob("*.json")):
        kind, rest = f.stem.split("_", 1)
        endpoint = next((e for e in sorted(SLICE_ENDPOINTS, key=len, reverse=True)
                         if rest.endswith("_" + e)), None)
        if not endpoint:
            continue
        mid = rest[: -(len(endpoint) + 1)]
        vertical, rest2 = mid.split("_", 1)
        duration = next((d for d in durs if rest2.endswith("_" + d)), None)
        sector = rest2[: -(len(duration) + 1)] if duration else rest2
        data = json.loads(f.read_text(encoding="utf-8"))
        slices.append(Slice(kind, vertical, sector, duration or "", endpoint, "", data))
    return slices


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["companies", "voices", "both"], default="both")
    ap.add_argument("--vertical", default="crypto", help="crypto|ai|trading|all")
    ap.add_argument("--sectors", nargs="*", help="override sector codes (default: enabled set)")
    ap.add_argument("--durations", nargs="*", default=DURATIONS)
    ap.add_argument("--limit", type=int, default=100, help="leaderboard rows (API caps at 100)")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--proxy-file", default=None, help="proxy list path (default: settings)")
    ap.add_argument("--browser", action="store_true", help="use the Camoufox/Cloudflare fallback")
    ap.add_argument("--dump-only", action="store_true", help="scrape to JSON files, skip the DB")
    ap.add_argument("--from-dump", help="re-load a dump dir into the DB without scraping")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if args.from_dump:
        dump_dir = Path(args.from_dump)
        run_id = dump_dir.name
        slices = _load_dump(dump_dir)
        logger.info("from-dump %s: %s slices", dump_dir, len(slices))
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = DATA_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        slices = await _scrape(args, run_id, out_dir)
        logger.info("sweep complete: %s slices scraped -> %s", len(slices), out_dir)
        if args.dump_only:
            return

    captures, rows, n_rows = _build(slices, run_id)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        session.add_all(captures)
        session.add_all(rows)
        await session.commit()
    logger.info("stored %s captures + %s mindshare rows (run_id=%s)", len(captures), len(rows), run_id)
    print(f"OK run_id={run_id}: {len(rows)} mindshare rows, {len(captures)} captures")


if __name__ == "__main__":
    asyncio.run(main())
