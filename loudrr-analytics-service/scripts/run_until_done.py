"""Crawl entrypoint for the server (Coolify): run the following-graph crawl in repeated
passes until EVERY smart-set member is captured, then exit 0. Resumable and idempotent —
each pass only picks up members still missing (last_crawled_at IS NULL), so a restart just
continues. Deferred members (API down for them this pass) are retried on the next pass.

Stops early and cleanly on budget/quota exhaustion, or if a pass makes zero progress twice
in a row (the few remaining members are permanently unreachable) so it can't loop forever.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update

from app.services.crawl import crawl
from app.db.session import SessionLocal, engine, Base
from app.db.models import SmartSetMember

SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed",
                         "smart_set_members.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("run_until_done")

PASS_BUDGET_USD = float(os.getenv("CRAWL_BUDGET_USD", "500"))


async def _maybe_reset() -> None:
    """One-time clean slate when CRAWL_RESET=1: wipe edges + clear last_crawled_at so every
    member re-crawls fresh (e.g. switching to the bulk-IDs endpoint). IDEMPOTENT — a marker
    row guards it so a mid-crawl container restart can NOT re-wipe and lose progress."""
    if os.getenv("CRAWL_RESET") != "1":
        return
    async with SessionLocal() as s:
        await s.execute(text("CREATE TABLE IF NOT EXISTS crawl_meta (k TEXT PRIMARY KEY, v TEXT)"))
        already = (await s.execute(text("SELECT 1 FROM crawl_meta WHERE k='reset_done'"))).first()
        if already:
            log.info("CRAWL_RESET set but already applied (marker present) — skipping wipe")
            return
        await s.execute(text("DELETE FROM edges"))
        await s.execute(update(SmartSetMember).values(last_crawled_at=None))
        await s.execute(text("INSERT INTO crawl_meta (k, v) VALUES ('reset_done', '1')"))
        await s.commit()
        log.info("CRAWL_RESET applied (once): edges wiped, all crawl flags cleared")


async def _ensure_seed() -> None:
    """Populate smart_set from the committed seed file on first boot (idempotent — only if the
    table is empty). Lets the container self-bootstrap on a fresh Postgres with no external
    data migration."""
    async with SessionLocal() as s:
        n = (await s.execute(select(func.count()).select_from(SmartSetMember))).scalar() or 0
        if n:
            log.info("smart_set already has %d members — skipping seed load", n)
            return
        members = json.load(open(SEED_FILE, encoding="utf-8"))
        for m in members:
            s.add(SmartSetMember(user_id=m["user_id"], username=m.get("username"),
                                 display_name=m.get("name"), category="unknown",
                                 is_seed=True, seed_source="master_v2"))
        await s.commit()
        log.info("seeded smart_set with %d members from %s", len(members), SEED_FILE)


async def _remaining() -> int:
    async with SessionLocal() as s:
        return (await s.execute(
            select(func.count()).select_from(SmartSetMember)
            .where(SmartSetMember.last_crawled_at.is_(None)))).scalar() or 0


def _merge(agg: dict, s: dict) -> None:
    """Accumulate a per-pass crawl() summary into the run totals (each pass has its own client,
    so counts/credits sum)."""
    for k in ("members_crawled", "edges_written", "accounts_gone_404", "failed_will_retry",
              "api_calls_ok", "api_attempts", "api_retries", "recrawl_events"):
        agg[k] = agg.get(k, 0) + (s.get(k) or 0)
    for kind, n in (s.get("api_failures") or {}).items():
        agg["api_failures"][kind] = agg["api_failures"].get(kind, 0) + n
    if s.get("credits_measured") is not None:
        agg["credits"] = agg.get("credits", 0) + s["credits_measured"]
    if s.get("usd_measured") is not None:
        agg["usd"] = agg.get("usd", 0) + s["usd_measured"]


async def _persist_report(rep: dict) -> None:
    """Write the run's performance report into crawl_runs (queryable in pgAdmin; share with the
    gateway team). Dialect-portable raw SQL."""
    cols = ("started_at", "finished_at", "duration_s", "passes", "members_crawled",
            "members_gone", "members_deferred",
            "edges", "api_calls", "api_attempts", "api_retries", "recrawl_events", "failures",
            "ids_per_min", "calls_per_min", "members_per_min", "credits", "usd", "stopped")
    async with SessionLocal() as s:
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS crawl_runs (" + ", ".join(
                f"{c} TEXT" if c in ("started_at", "finished_at", "failures", "stopped")
                else f"{c} REAL" for c in cols) + ")"))
        await s.execute(text(
            f"INSERT INTO crawl_runs ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"),
            rep)
        await s.commit()


async def main() -> None:
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    async with engine.begin() as conn:       # ensure tables exist on a fresh Postgres
        await conn.run_sync(Base.metadata.create_all)
    await _maybe_reset()
    await _ensure_seed()

    agg: dict = {"api_failures": {}}
    no_progress = 0
    pass_no = 0
    stopped = "complete"
    while True:
        rem = await _remaining()
        if rem == 0:
            log.info("ALL MEMBERS CRAWLED — done.")
            break
        pass_no += 1
        log.info("=== crawl pass %d: %d members remaining ===", pass_no, rem)
        summary = await crawl(budget_usd=PASS_BUDGET_USD)
        _merge(agg, summary)
        after = await _remaining()
        log.info("pass %d done: %d -> %d remaining (%s)", pass_no, rem, after, summary.get("stopped"))

        if summary.get("stopped") in ("budget", "quota", "balance_stale"):
            stopped = summary["stopped"]
            log.warning("stopping: %s (re-run to continue once resolved)", stopped)
            break
        if after >= rem:
            no_progress += 1
            if no_progress >= 2:
                stopped = "stuck"
                log.error("two passes with no progress — %d members unreachable; stopping.", after)
                break
        else:
            no_progress = 0

    # ---- performance report (for the gateway team) ----
    dur = time.monotonic() - t0
    deferred = await _remaining()
    crawled = agg.get("members_crawled", 0)
    rep = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(dur, 1), "passes": pass_no,
        "members_crawled": crawled, "members_gone": agg.get("accounts_gone_404", 0),
        "members_deferred": deferred, "edges": agg.get("edges_written", 0),
        "api_calls": agg.get("api_calls_ok", 0), "api_attempts": agg.get("api_attempts", 0),
        "api_retries": agg.get("api_retries", 0), "recrawl_events": agg.get("recrawl_events", 0),
        "failures": json.dumps(agg["api_failures"]),
        "ids_per_min": round(agg.get("edges_written", 0) / dur * 60) if dur > 0 else 0,
        "calls_per_min": round(agg.get("api_calls_ok", 0) / dur * 60, 1) if dur > 0 else 0,
        "members_per_min": round(crawled / dur * 60, 2) if dur > 0 else 0,
        "credits": round(agg.get("credits", 0), 1), "usd": round(agg.get("usd", 0), 2),
        "stopped": stopped,
    }
    reachable = crawled + deferred  # exclude gone/deleted accounts
    clean_pct = (crawled / reachable * 100) if reachable else 100.0
    bar = "=" * 62
    log.info("\n%s\nCRAWL PERFORMANCE REPORT (followings_ids)  %s\n%s"
             "\n duration      : %.0f min (%d pass)"
             "\n members       : crawled=%d gone=%d deferred=%d"
             "\n completion    : %.1f%% of reachable members crawled cleanly (to has_next_page=false)"
             "\n edges (IDs)   : %d"
             "\n gateway calls : ok=%d attempts=%d retries=%d  failures=%s"
             "\n recrawls      : %d (transient-error driven)"
             "\n throughput    : %d ids/min | %.1f calls/min | %.2f members/min"
             "\n cost          : %.0f credits (~$%.2f)"
             "\n stopped       : %s\n%s",
             bar, rep["finished_at"], bar, dur / 60, pass_no, rep["members_crawled"],
             rep["members_gone"], rep["members_deferred"], clean_pct, rep["edges"],
             rep["api_calls"], rep["api_attempts"], rep["api_retries"], rep["failures"],
             rep["recrawl_events"], rep["ids_per_min"], rep["calls_per_min"],
             rep["members_per_min"], rep["credits"], rep["usd"], stopped, bar)
    try:
        await _persist_report(rep)
        log.info("performance report saved to crawl_runs table")
    except Exception as e:  # noqa: BLE001
        log.warning("could not persist report: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
