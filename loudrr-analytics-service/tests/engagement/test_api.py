"""TDD spec — GET /v1/smart-engagement serving contract.

The endpoint the heatmap calls. Invariants:
  * cell value = COUNT(DISTINCT engager) per day (same engager twice in a day counts once),
  * lookup is case-insensitive on userName and resolvable by target_id via smart_set,
  * sparse response: only non-zero days in `counts`, keys are ISO YYYY-MM-DD,
  * NEVER 500s: unknown handle -> 200 coverage:"none"; internal failure -> 200 coverage:"none".
    (The public funnel must be untouchable by this feature.)
"""
from datetime import date, datetime

import httpx
import pytest

from app.db.models import SmartSetMember
from app.engagement import models as em

D1, D2 = date(2026, 6, 20), date(2026, 6, 21)


def _edge(tweet_id, engager, target_id="7007", target_username="viewedguy",
          kind="reply", day=D1):
    return em.EngEdge(tweet_id=tweet_id, engager_id=engager, target_id=target_id,
                      target_username=target_username, kind=kind, day=day,
                      ts=datetime(day.year, day.month, day.day, 12, 0, 0))


async def _seed(SessionLocal, rows):
    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()


@pytest.fixture
async def client(eng_db):
    from app.main import app
    import app.engagement.api as eng_api
    eng_api._coverage_cache = None  # 6h module cache must not leak across test DBs
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, eng_db
    eng_api._coverage_cache = None


async def test_counts_distinct_engagers_per_day(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        _edge("t1", "e1", day=D1),
        _edge("t2", "e1", day=D1, kind="quote"),   # same engager same day -> still 1
        _edge("t3", "e2", day=D1),
        _edge("t4", "e1", day=D2),
    ])
    r = await c.get("/v1/smart-engagement", params={"userName": "ViewedGuy"})  # case-insensitive
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"] == "tracked"
    assert body["counts"] == {"2026-06-20": 2, "2026-06-21": 1}
    assert body["total"] == 3
    assert body["firstData"] == "2026-06-20"


async def test_resolves_by_target_id_when_username_differs(client):
    # engagement recorded before a handle change: target_id matches smart_set, username doesn't
    c, SessionLocal = client
    await _seed(SessionLocal, [
        SmartSetMember(user_id="7007", username="newhandle", score=1.0),
        _edge("t1", "e1", target_id="7007", target_username="oldhandle"),
    ])
    r = await c.get("/v1/smart-engagement", params={"userName": "newhandle"})
    assert r.status_code == 200
    assert r.json()["coverage"] == "tracked"
    assert r.json()["counts"] == {"2026-06-20": 1}


async def test_unknown_handle_returns_200_none(client):
    c, _ = client
    r = await c.get("/v1/smart-engagement", params={"userName": "nobody_ever_engaged"})
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"] == "none"
    assert body["counts"] == {}
    assert body["total"] == 0


async def test_missing_param_returns_200_none(client):
    c, _ = client
    r = await c.get("/v1/smart-engagement")
    assert r.status_code == 200
    assert r.json()["coverage"] == "none"


async def test_internal_failure_returns_200_none(client, monkeypatch):
    # the handler must swallow ANY exception — the funnel can never be 500'd by this feature
    c, _ = client
    import app.engagement.api as eng_api

    class _Boom:
        def __call__(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(eng_api, "SessionLocal", _Boom())
    r = await c.get("/v1/smart-engagement", params={"userName": "whoever"})
    assert r.status_code == 200
    assert r.json()["coverage"] == "none"


async def test_recycled_handle_does_not_blend_histories(client):
    """Once a handle resolves to an id, ONLY that id's edges count — a recycled handle's
    previous owner's edges must never appear in the new owner's heatmap (finding #12)."""
    c, SessionLocal = client
    await _seed(SessionLocal, [
        SmartSetMember(user_id="B", username="alpha", score=1.0),   # current owner of @alpha
        _edge("t_old", "e1", target_id="AAA", target_username="alpha"),  # previous owner's edge
        _edge("t_new", "e2", target_id="B", target_username="alpha"),
    ])
    r = await c.get("/v1/smart-engagement", params={"userName": "alpha"})
    body = r.json()
    assert body["total"] == 1            # only B's edge; AAA's history excluded
    assert body["userId"] == "B"


async def test_panel_coverage_annotations(client):
    """Timelines are page-capped newest-first, so old days are only seen by the slice of the
    panel whose history reaches back. The endpoint must expose per-day coverage + the >=90%
    boundary so the UI can render 'not tracked yet' instead of a fake low-activity gradient."""
    from datetime import timedelta
    c, SessionLocal = client
    deep = datetime.now() - timedelta(days=300)   # covers (almost) the whole window
    shallow = datetime.now() - timedelta(days=10)  # covers only the last 10 days
    await _seed(SessionLocal, [
        em.EngTweetRaw(tweet_id="r1", member_id="m1", created_at=deep, raw={}),
        em.EngTweetRaw(tweet_id="r2", member_id="m1", created_at=datetime.now(), raw={}),
        em.EngTweetRaw(tweet_id="r3", member_id="m2", created_at=shallow, raw={}),
        _edge("t1", "m1", day=date.today()),
    ])
    r = await c.get("/v1/smart-engagement", params={"userName": "viewedguy"})
    body = r.json()
    assert body["panelSize"] == 2
    cov = body["panelCoverage"]
    assert cov[date.today().isoformat()] == 1.0                        # both members cover today
    assert cov[(date.today() - timedelta(days=100)).isoformat()] == 0.5  # only m1 reaches back
    assert cov[(date.today() - timedelta(days=350)).isoformat()] == 0.0  # nobody that far back
    # >=90% boundary = the day m2's history starts
    assert body["trackedSince"] == (date.today() - timedelta(days=10)).isoformat()


async def test_panel_coverage_failure_degrades(client, monkeypatch):
    # a broken coverage computation must never take down the heatmap payload
    c, SessionLocal = client
    await _seed(SessionLocal, [_edge("t1", "e1", day=D1)])
    import app.engagement.api as eng_api

    async def _boom():
        raise RuntimeError("coverage down")

    monkeypatch.setattr(eng_api, "_panel_coverage", _boom)
    r = await c.get("/v1/smart-engagement", params={"userName": "viewedguy"})
    body = r.json()
    assert body["coverage"] == "tracked"          # counts still served
    assert body["panelCoverage"] == {}
    assert body["trackedSince"] is None


async def test_window_is_bounded_to_a_year(client):
    c, SessionLocal = client
    await _seed(SessionLocal, [
        _edge("t_old", "e1", day=date(2024, 1, 1)),     # ancient -> outside window
        _edge("t_new", "e2", day=date.today()),
    ])
    r = await c.get("/v1/smart-engagement", params={"userName": "viewedguy"})
    body = r.json()
    assert "2024-01-01" not in body["counts"]
    assert body["counts"][date.today().isoformat()] == 1
