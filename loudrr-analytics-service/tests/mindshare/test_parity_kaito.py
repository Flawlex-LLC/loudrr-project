"""TDD spec — "be like Kaito": our live crypto leaderboard must resemble Kaito's.

Read-only against ``data/harvest.db`` (raw sqlite3 — never mutates). Skips if the pipeline hasn't
been run yet. These are the acceptance gates that say our numbers are credible enough to ship:
  * strong top-10 set overlap with Kaito's companies leaderboard,
  * the obvious majors (BTC, SOL) near the top,
  * no common-word attribution noise polluting the top-10.
Re-run `python -m app.mindshare.service --vertical crypto` then these reflect the latest output.
"""
import os
import sqlite3

import pytest

DB = "data/harvest.db"
# Confirmed bare-word false positives we drove out (must never re-appear via bare text).
# (LUNA/TON/etc. are excluded — they can legitimately appear via a $cashtag.)
NOISE = {"NEAR", "CAP", "BASE"}
OVERLAP_MIN = 8   # of top-10. Calibrated model (log engagement + 48h recency) hits 9/10; 8 = guard.


def _conn():
    if not os.path.exists(DB):
        pytest.skip("no harvest.db")
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _ours(c, sector="ALL", window="7d", n=15):
    st = c.execute("select max(snap_ts) from ms_snapshot where vertical='crypto'").fetchone()[0]
    if not st:
        pytest.skip("mindshare pipeline not run yet (no ms_snapshot)")
    return [r[0] for r in c.execute(
        "select entity_id from ms_snapshot where vertical='crypto' and sector=? and window=? "
        "and snap_ts=? order by rank limit ?", (sector, window, st, n))]


def _kaito(c, sector="ALL", n=15):
    rid = c.execute("select max(run_id) from kaito_mindshare").fetchone()[0]
    if not rid:
        pytest.skip("no kaito_mindshare reference")
    return [r[0] for r in c.execute(
        "select entity_id from kaito_mindshare where run_id=? and kind='companies' and "
        "vertical='crypto' and sector=? and duration='7d' order by rank limit ?", (rid, sector, n))]


def test_top10_overlap_with_kaito():
    c = _conn()
    ours, kaito = _ours(c)[:10], _kaito(c)[:10]
    overlap = set(ours) & set(kaito)
    assert len(overlap) >= OVERLAP_MIN, f"only {len(overlap)}/10 overlap; ours={ours} kaito={kaito}"


def test_majors_near_top():
    c = _conn()
    top5 = set(_ours(c)[:5])
    assert "BTC" in top5 and "SOL" in top5, f"BTC/SOL not in our top-5: {top5}"


def test_no_common_word_noise_in_top10():
    c = _conn()
    top10 = set(_ours(c)[:10])
    leaked = top10 & NOISE
    assert not leaked, f"attribution noise in top-10: {leaked}"


def test_mindshare_normalized_per_niche():
    c = _conn()
    st = c.execute("select max(snap_ts) from ms_snapshot where vertical='crypto'").fetchone()[0]
    if not st:
        pytest.skip("no snapshot")
    # a sector that fits under the row cap should sum to ~1.0
    total = c.execute("select sum(mindshare) from ms_snapshot where vertical='crypto' "
                      "and sector='ALL' and window='7d' and snap_ts=?", (st,)).fetchone()[0]
    assert total is None or 0.95 <= total <= 1.05, f"ALL/7d mindshare sums to {total}, expected ~1"
