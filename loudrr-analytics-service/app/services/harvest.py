"""TweetScout/Sorsa harvest orchestration — the ONE-TIME 10k-request extraction.

Implements docs/harvest_plan.md: an instrumented pilot that measures the unknowns and
gates the spend, then a crash-safe best-first /top-followers snowball that reconstructs
their ranked smart-set M with per-account scores. Designed around the adversarially-
verified plan: best-first front-loads the high-out-degree influencer core, diversity
injection avoids clique-trapping, and a flattening novelty curve means "core drained"
(redirect to breadth) NOT "M covered".

Crash-safety (the spend is irreversible): cumulative API spend is restored from the
auto-written call log; the queried-id set is checkpointed to disk and an id is recorded
as queried BEFORE its call, so a crash can lose one account's data but NEVER double-spend.
"""
from __future__ import annotations

import heapq
import json
import logging
import os
from collections import deque

from sqlalchemy import select

from app.clients.sorsa import (
    CALL_LOG,
    SorsaBudgetExhausted,
    SorsaClient,
    SorsaQuotaError,
    SorsaTransientError,
)
from app.core.config import settings
from app.db.models import HarvestedScore
from app.db.session import SessionLocal
from app.services.calibration import _upsert_harvested
from app.services.seed import categorize

logger = logging.getLogger(__name__)

# Anchored to the repo root (not CWD) so a resume from anywhere finds the same state.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_DIR = os.path.join(_REPO_ROOT, "data", "harvest_state")
QUERIED_FILE = "queried_ids.txt"


# ---- pure, unit-testable helpers ---------------------------------------

def gate_split(n: float, pool: int) -> tuple[int, int]:
    """Given measured mid-stratum net-new-uniques/call N, split the shared snowball/
    breadth ``pool`` into (snowball_requests, breadth_requests) per the plan's GATE.
    Refuted assumption #2 ("core is enough") => breadth is mandatory, never zero."""
    if n >= 8:        # core rich -> deep snowball, minimal breadth
        snow = round(pool * 0.88)
    elif n >= 3:      # moderate overlap -> cap snowball, real breadth (expected case)
        snow = round(pool * 0.74)
    else:             # core small/saturates fast -> shallow snowball, heavy breadth
        snow = round(pool * 0.49)
    return snow, pool - snow


def holdout_excluded(uid: str, mod: int = 20) -> bool:
    """Deterministic ~1/mod holdout (by id hash) excluded from the calibration FIT so
    parity is measured leakage-free. Stable across runs; no RNG."""
    return uid.isdigit() and int(uid) % mod == 0


def novelty_summary(net_new: list[int], lo: int = 200, hi: int = 300) -> dict:
    """N = mean net-new-uniques/call over the steady-state window [lo,hi) (after seed
    novelty settles). Falls back to the last third if the pilot was shorter."""
    if not net_new:
        return {"n": 0.0, "window": (0, 0), "calls": 0}
    if len(net_new) >= hi:
        window = net_new[lo:hi]
        bounds = (lo, hi)
    else:
        start = max(1, (2 * len(net_new)) // 3)
        window = net_new[start:]
        bounds = (start, len(net_new))
    n = sum(window) / len(window) if window else 0.0
    return {"n": round(n, 3), "window": bounds, "calls": len(net_new)}


def edge_local_verdict(by_uid: dict[str, list[float]], rel_tol: float = 0.05,
                       min_multi: int = 5) -> dict:
    """Decide GLOBAL vs EDGE-LOCAL: for uids seen as a top-follower of >=2 parents, is
    the score invariant across parents? High invariance => global (a reusable label
    table). Variance => edge-local (must store per-(parent,follower) edges instead)."""
    multi = {u: s for u, s in by_uid.items() if len(s) >= 2}
    if len(multi) < min_multi:
        return {"decided": False, "edge_local": False, "multi_parent_accounts": len(multi),
                "note": "too few multi-parent observations to decide; default global"}
    invariant = 0
    for scores in multi.values():
        mean = sum(scores) / len(scores)
        spread = (max(scores) - min(scores)) / mean if mean else 0.0
        if spread <= rel_tol:
            invariant += 1
    frac = invariant / len(multi)
    return {"decided": True, "edge_local": frac < 0.7, "invariant_fraction": round(frac, 3),
            "multi_parent_accounts": len(multi)}


def count_api_calls_in_log(path: str = CALL_LOG) -> int:
    """Restore cumulative API spend from the auto-written call log (excludes the FREE
    app.sorsa.io dashboard reads). The durable spend counter for crash-safe resume."""
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                if json.loads(line).get("path") != "app.sorsa.io/profile":
                    n += 1
            except Exception:  # noqa: BLE001
                continue
    return n


# ---- state / checkpoint -------------------------------------------------

def _queried_path(state_dir: str) -> str:
    return os.path.join(state_dir, QUERIED_FILE)


def _load_set(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {ln.strip() for ln in f if ln.strip()}


def _load_ints(path: str) -> list[int]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [int(ln) for ln in f if ln.strip().lstrip("-").isdigit()]


def _load_queried(state_dir: str) -> set[str]:
    return _load_set(_queried_path(state_dir))


async def _load_harvested_ids() -> set[str]:
    async with SessionLocal() as session:
        return set((await session.execute(select(HarvestedScore.user_id))).scalars())


async def _frontier_from_db(queried: set[str]) -> list[tuple[float, str]]:
    """Rebuild the discovered frontier on resume/breadth: every numeric, not-yet-queried
    account in HarvestedScore as (score, uid). Without this, a resume or Phase 5 would
    re-seed the heap only from the original seeds and lose the whole discovered frontier
    (review finding #3)."""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(HarvestedScore.user_id, HarvestedScore.sorsa_score))).all()
    return [(float(s or 0.0), uid) for uid, s in rows
            if uid and uid.isdigit() and uid not in queried]


def _tf_rows(users: list[dict]) -> tuple[list[dict], list[tuple[str, float]]]:
    """Parse a /top-followers payload -> (upsert rows, [(uid, score)] for the frontier).
    Rejects entries without a numeric id or a numeric score (never username into the PK)."""
    rows, edges = [], []
    for tf in users:
        uid = str(tf.get("id") or "")
        sc = tf.get("score")
        if not uid.isdigit() or not isinstance(sc, (int, float)) or isinstance(sc, bool):
            continue
        # tolerant field reads (#10): live schema uses display_name/description, but
        # fall back to name/bio so categorization degrades gracefully, never crashes.
        name = tf.get("display_name") or tf.get("name")
        bio = tf.get("description") or tf.get("bio")
        rows.append({
            "user_id": uid, "username": tf.get("username") or tf.get("userName"),
            "sorsa_score": float(sc), "category": categorize(name, bio).value,
            "source": "top_follower",
        })
        edges.append((uid, float(sc)))
    return rows, edges


# ---- the instrumented pilot (gates the main spend) ----------------------

def _append_line(path: str, value: str) -> None:
    # flush to OS cache (survives a process crash); no per-line fsync — pilot state is
    # recomputable/idempotent on resume, so the per-call disk barrier isn't worth it.
    with open(path, "a", encoding="utf-8") as f:
        f.write(value + "\n"); f.flush()


async def run_pilot(client: SorsaClient, targets: list[str], *, scale_probe: int = 60,
                    pool: int = 8160, state_dir: str = STATE_DIR) -> dict:
    """Phase 2: measure the unknowns on a stratified target list, then return the GATE.

    Sequentially /top-followers each target (NOT best-first — unbiased novelty curve),
    tracking net-new uniques/call and per-uid scores across parents. Then /score a sample
    that also appeared as top-followers to settle GLOBAL-vs-edge-local and re-confirm the
    unified scale. CRASH-SAFE (finding #5): each target/uid is recorded BEFORE its call
    and skipped on resume (no double-spend); the `seen` baseline + net_new are persisted
    so resume neither re-spends nor lets the pilot's own upserts pollute its novelty."""
    os.makedirs(state_dir, exist_ok=True)
    done_f = os.path.join(state_dir, "pilot_done.txt")
    seen_f = os.path.join(state_dir, "pilot_seen.txt")
    netnew_f = os.path.join(state_dir, "pilot_netnew.txt")
    scale_done_f = os.path.join(state_dir, "pilot_scale_done.txt")

    done = _load_set(done_f)
    seen: set[str] = _load_set(seen_f)            # frozen-but-growing; NOT reloaded from DB
    net_new: list[int] = _load_ints(netnew_f)
    by_uid: dict[str, list[float]] = {}
    appeared: dict[str, float] = {}
    linchpin_ok = bool(seen)

    for handle in targets:
        h = handle.lstrip("@")
        if h in done:
            continue
        _append_line(done_f, h)                   # record BEFORE spend (no double-spend)
        try:
            users = await client.top_followers(username=h)
        except (SorsaTransientError, SorsaBudgetExhausted):
            continue
        except SorsaQuotaError:
            break
        rows, edges = _tf_rows(users)
        if edges:
            linchpin_ok = True
        new = 0
        for uid, sc in edges:
            by_uid.setdefault(uid, []).append(sc)
            appeared[uid] = sc
            if uid not in seen:
                seen.add(uid); _append_line(seen_f, uid); new += 1
        _append_line(netnew_f, str(new))
        net_new.append(new)
        await _upsert_harvested(rows)

    # GLOBAL vs EDGE-LOCAL + scale re-confirm: /score a sample that appeared as top-followers
    scale_done = _load_set(scale_done_f)
    scale_pairs: list[dict] = []
    for uid in list(appeared)[:scale_probe]:
        if uid in scale_done:
            continue
        _append_line(scale_done_f, uid)
        try:
            api_score = await client.score(user_id=uid)
        except (SorsaTransientError, SorsaBudgetExhausted):
            continue
        except SorsaQuotaError:
            break
        scale_pairs.append({"user_id": uid, "top_follower_score": appeared[uid],
                            "api_score": api_score})

    novelty = novelty_summary(net_new)
    edge = edge_local_verdict(by_uid)
    snow, breadth = gate_split(novelty["n"], pool)
    return {
        "linchpin_ok": linchpin_ok,
        "novelty": novelty,
        "edge_local": edge,
        "scale_pairs_sample": scale_pairs[:10],
        "scale_pairs_count": len(scale_pairs),
        "unique_discovered": len(seen),
        "gate": {"net_new_per_call": novelty["n"], "snowball_ceiling_share": snow,
                 "breadth_ceiling_share": breadth},
        "requests_spent": client.requests_spent,
    }


# ---- the snowball -------------------------------------------------------

async def snowball_harvest(
    client: SorsaClient,
    seed_frontier: list[tuple[float, str]],
    *,
    ceiling: int,
    state_dir: str = STATE_DIR,
    diversity_seeds: list[str] | None = None,
    diversity_every: int = 12,
    soft_stop_window: int = 200,
    soft_stop_threshold: float = 2.5,
    soft_stop_min_score: float | None = None,
    strategy: str = "breadth",
) -> dict:
    """/top-followers snowball over the follow graph.

    ``strategy`` (LIVE-VALIDATED default 'breadth'):
      * 'breadth'    — FIFO over diverse accounts. The high-score crypto core is densely
                       inter-followed, so best-first re-mines it and finds almost nothing
                       new (measured LIVE: ~0.1 new/call). Breadth over diverse seeds gave
                       ~4 new/call. So breadth is the discovery workhorse.
      * 'best_first' — max-heap by score (kept for completeness; redundant for discovery).

    Stops at the cumulative ``ceiling`` (client.requests_spent, restored across runs), an
    empty frontier, or the soft-stop (rolling net-new/call < threshold — saturated).
    Resumable + crash-safe: queried ids are checkpointed (fsync) BEFORE each call.
    """
    breadth = strategy != "best_first"
    os.makedirs(state_dir, exist_ok=True)
    queried = _load_queried(state_dir)

    pushed: set[str] = set()              # frontier-admission set + novelty baseline
    heap: list[tuple[float, str]] = []
    fifo: deque[tuple[float, str]] = deque()

    def push(score: float, uid: str) -> bool:
        if uid in queried or uid in pushed:
            return False
        pushed.add(uid)
        (fifo.append if breadth else (lambda x: heapq.heappush(heap, (-x[0], x[1]))))((score, uid))
        return True

    # Seed: diverse seeds FIRST (most productive in breadth), then discovered-but-unqueried.
    for score, uid in list(seed_frontier) + await _frontier_from_db(queried):
        push(score, uid)
    div_queue = deque(u for u in (diversity_seeds or []) if u not in queried)
    novelty: deque[int] = deque(maxlen=soft_stop_window)

    start_spent = client.requests_spent
    calls = 0
    # BATCHED WRITES (the host-load fix): buffer parsed rows and COMMIT once per ~300
    # rows (~17 calls) instead of per call. Collapses the per-call WAL/fsync storm that
    # destabilized Postgres. Crash-safe: queried ids are recorded before spend, so an
    # unflushed batch only re-harvests a few accounts on resume — never double-spends.
    FLUSH_ROWS, FSYNC_EVERY = 300, 25
    buffer: list[dict] = []
    qf = open(_queried_path(state_dir), "a", encoding="utf-8")
    stopped = "ceiling"

    def _next_from_frontier() -> tuple[str | None, float]:
        if breadth:
            while fifo:
                sc, cand = fifo.popleft()
                if cand not in queried:
                    return cand, sc
        else:
            while heap:
                negs, cand = heapq.heappop(heap)
                if cand not in queried:
                    return cand, -negs
        return None, 0.0

    async def _flush(session) -> None:
        if not buffer:
            return
        try:
            await _upsert_harvested(buffer, session=session)
            await session.commit()
            buffer.clear()
        except Exception as e:  # noqa: BLE001 — keep buffer, retry next flush
            logger.warning("flush failed (%d rows held): %s", len(buffer), e)
            await session.rollback()

    try:
        async with SessionLocal() as session:
            try:
                while client.requests_spent < ceiling:
                    # --- select next node; can never busy-spin (#2) ---
                    node, node_score, from_div = None, 0.0, False
                    while div_queue and div_queue[0] in queried:
                        div_queue.popleft()
                    # best_first needs forced diversity injection; breadth is inherently diverse
                    if (not breadth) and (calls % diversity_every == 0) and div_queue:
                        node, from_div = div_queue.popleft(), True
                    else:
                        node, node_score = _next_from_frontier()
                        if node is None:             # frontier drained -> fall back to diversity
                            while div_queue and div_queue[0] in queried:
                                div_queue.popleft()
                            if div_queue:
                                node, from_div = div_queue.popleft(), True
                            else:
                                stopped = "frontier_empty"
                                break
                    # --- soft-stop: breadth=novelty-only (saturated); best_first adds floor (#6)
                    if (len(novelty) == soft_stop_window
                            and (sum(novelty) / soft_stop_window) < soft_stop_threshold
                            and (breadth or (not from_div
                                 and (soft_stop_min_score is None or node_score < soft_stop_min_score)))):
                        stopped = "soft_stop_saturated"
                        break
                    # --- record queried BEFORE spending; flush=>OS cache (survives process
                    #     crash); fsync every K (power-loss durability) — not per call (#2 IO)
                    queried.add(node)
                    qf.write(node + "\n"); qf.flush()
                    if calls % FSYNC_EVERY == 0:
                        os.fsync(qf.fileno())
                    # --- spend; one bad account must NEVER abort the irreversible run (#7) ---
                    try:
                        users = await client.top_followers(user_id=node)
                    except SorsaBudgetExhausted:
                        stopped = "budget_exhausted"; break
                    except SorsaQuotaError:
                        stopped = "quota_exhausted"; raise      # terminal -> propagate
                    except Exception as e:  # noqa: BLE001 (transient/forbidden) -> skip one
                        logger.warning("skip %s: %s", node, e)
                        calls += 1
                        continue
                    rows, edges = _tf_rows(users)
                    new = sum(push(sc, uid) for uid, sc in edges)
                    buffer.extend(rows)
                    if len(buffer) >= FLUSH_ROWS:
                        await _flush(session)
                    novelty.append(int(new))
                    calls += 1
            finally:
                await _flush(session)               # always persist the last batch
    finally:
        try:
            os.fsync(qf.fileno())
        except Exception:  # noqa: BLE001
            pass
        qf.close()

    return {
        "strategy": strategy,
        "stopped": stopped,
        "calls_this_run": calls,
        "requests_spent_this_phase": client.requests_spent - start_spent,
        "total_requests_spent": client.requests_spent,
        "frontier_size": len(pushed),
        "queried": len(queried),
    }
