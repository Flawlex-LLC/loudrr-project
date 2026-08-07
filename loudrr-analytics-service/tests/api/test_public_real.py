"""TDD spec — the real-data public API (/v1/leaderboard, /v1/profile, /v1/search).

Leaderboard/search serve from ranked_accounts. /profile scores ANY resolvable X account:
ranked accounts fast-path the crawl table, everyone else is resolved + scored on our own
graph (always fresh) with a 24h-cached profile card. Only genuinely non-existent handles
are found:false.
"""
import httpx
import pytest

from app.db.models import Edge, ProfileCache, RankedAccount, SmartSetMember


def _acct(rank, uid, uname, score=1000, elite=100, **kw):
    return RankedAccount(
        user_id=uid, rank=rank, username=uname.lower(), display_username=uname,
        name=kw.get("name", uname.title()), bio=kw.get("bio"), followers=kw.get("followers", 5000),
        following=kw.get("following", 300), verified=kw.get("verified", False),
        elite_followers=elite, raw_score=float(score), score=score,
        categories=kw.get("categories"),
    )


@pytest.fixture(autouse=True)
def _no_real_gateway(monkeypatch):
    """Default: the gateway resolves NOTHING (so no test hits the network). Individual tests
    opt in via _fake_gateway()."""
    async def _none(self, username):
        return None
    monkeypatch.setattr("app.clients.twitterapi.TwitterAPIClient.get_user_info", _none)


def _fake_gateway(monkeypatch, profiles: dict):
    """Map handle(lower) -> profile dict; also counts calls for cache assertions."""
    calls = {"n": 0}

    async def _get(self, username):
        calls["n"] += 1
        return profiles.get(username.lstrip("@").lower())

    monkeypatch.setattr("app.clients.twitterapi.TwitterAPIClient.get_user_info", _get)
    return calls


@pytest.fixture
async def client():
    """Fresh public tables in the shared temp DB (root conftest sets DATABASE_URL)."""
    from sqlalchemy import delete
    from app.db.session import Base, SessionLocal, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        for T in (RankedAccount, Edge, ProfileCache, SmartSetMember):
            await s.execute(delete(T))
        await s.commit()
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, SessionLocal


async def _seed(SessionLocal, rows):
    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()


async def test_leaderboard_ordered_and_shaped(client):
    c, S = client
    await _seed(S, [_acct(2, "2", "bob", score=2000), _acct(1, "1", "Alice", score=3000),
                    _acct(3, "3", "carol", score=1000)])
    r = await c.get("/v1/leaderboard", params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert [i["rank"] for i in body["items"]] == [1, 2]
    top = body["items"][0]
    assert top["userName"] == "Alice"          # display casing preserved
    assert top["score"] == 3000
    assert "eliteFollowers" in top and "followers" in top


async def test_profile_found_with_notable_followers(client):
    c, S = client
    # notable followers come from the REAL follow graph (edges: member -> followee),
    # highest-scored first — not the sparse twitterscore_follows vendor table
    await _seed(S, [
        _acct(1, "10", "hero", score=4000, elite=250, bio="builder", categories="Influencers"),
        _acct(3, "20", "fan_low", score=1500),
        _acct(2, "30", "fan_high", score=2500),
        Edge(follower_id="20", followee_id="10"),
        Edge(follower_id="30", followee_id="10"),
    ])
    r = await c.get("/v1/profile", params={"userName": "HERO"})   # case-insensitive
    assert r.status_code == 200
    b = r.json()
    assert b["found"] is True
    assert (b["rank"], b["score"], b["eliteFollowers"]) == (1, 4000, 250)
    assert b["bio"] == "builder"
    assert b["percentile"] > 99                 # rank 1 of 3
    nf = b["notableFollowers"]
    assert [f["userName"] for f in nf] == ["fan_high", "fan_low"]  # ordered by score desc
    assert nf[0]["score"] == 2500


async def test_profile_nonexistent_handle_is_found_false(client):
    # gateway can't resolve it (default _no_real_gateway) -> genuinely not on X
    c, _ = client
    r = await c.get("/v1/profile", params={"userName": "nobody"})
    assert r.status_code == 200
    assert r.json() == {"found": False, "userName": "nobody"}


async def test_profile_scores_any_resolvable_account(client, monkeypatch):
    """A real account NOT in ranked_accounts still gets a full scored card — resolved via
    gateway, scored on our own graph, ranked by where its score would sit. No 'not scored'."""
    c, S = client
    _fake_gateway(monkeypatch, {"degentrader": {
        "id": "999", "userName": "DegenTrader", "name": "Degen Trader",
        "followers": 12000, "following": 800, "description": "aping since 2021",
        "isBlueVerified": True, "profilePicture": "https://img/d.png", "location": "Solana",
    }})
    # seed a ranked board + a smart-set member who follows @degentrader (gives it real backing)
    await _seed(S, [
        _acct(1, "10", "hero", score=4000),
        _acct(2, "20", "mid", score=2000),
        SmartSetMember(user_id="20", username="mid", score=180.0),
        Edge(follower_id="20", followee_id="999"),   # @mid follows @degentrader
    ])
    r = await c.get("/v1/profile", params={"userName": "DegenTrader"})
    b = r.json()
    assert b["found"] is True
    assert b["userId"] == "999"
    assert b["followers"] == 12000 and b["following"] == 800
    assert b["verified"] is True
    assert b["score"] > 0                         # real smart backing -> positive score (no floor)
    assert b["eliteFollowers"] == 1              # one smart follower (@mid)
    assert b["rank"] >= 1 and 0 <= b["percentile"] <= 100
    # notable follower surfaces from the real graph
    assert any(f["userName"] == "mid" for f in b["notableFollowers"])


async def test_zero_score_for_account_with_no_smart_followers(client, monkeypatch):
    # a real account nobody in the smart set follows scores 0 — no floor (Sorsa/TweetScout have
    # none either); it's still 'found', just unbacked.
    c, S = client
    _fake_gateway(monkeypatch, {"randomguy": {
        "id": "777", "userName": "randomguy", "name": "Random", "followers": 40}})
    await _seed(S, [_acct(1, "10", "hero", score=4000)])
    r = await c.get("/v1/profile", params={"userName": "randomguy"})
    b = r.json()
    assert b["found"] is True
    assert b["score"] == 0.0 and b["eliteFollowers"] == 0


async def test_smart_set_cutoff_keeps_only_top_rank_voters(client, monkeypatch):
    """SMART_SET_CUTOFF drops the low-pr_rank tail from raw score + smart_followers; NULL pr_rank
    rows are always kept (pre-ranking fixtures). Default None = all voters (historical behavior)."""
    from app.core.config import settings
    from app.services import score as scoring
    _, S = client
    await _seed(S, [
        SmartSetMember(user_id="20", username="hi", score=100.0, pr_rank=10),
        SmartSetMember(user_id="30", username="lo", score=100.0, pr_rank=50000),
        Edge(follower_id="20", followee_id="999"),
        Edge(follower_id="30", followee_id="999"),
    ])
    monkeypatch.setattr(settings, "smart_set_cutoff", None)
    r = await scoring.score_for("999")
    assert (r["smart_followers"], r["raw"]) == (2, 200.0)      # both voters count

    monkeypatch.setattr(settings, "smart_set_cutoff", 40000)
    r = await scoring.score_for("999")
    assert (r["smart_followers"], r["raw"]) == (1, 100.0)      # rank-50000 voter dropped


async def test_profile_card_cached_24h_no_second_gateway_hit(client, monkeypatch):
    c, S = client
    calls = _fake_gateway(monkeypatch, {"cachedkol": {
        "id": "555", "userName": "cachedkol", "name": "Cached", "followers": 3000}})
    await _seed(S, [_acct(1, "10", "hero", score=4000)])
    await c.get("/v1/profile", params={"userName": "cachedkol"})
    await c.get("/v1/profile", params={"userName": "cachedkol"})
    assert calls["n"] == 1                        # second lookup served from profile_cache


async def test_search_matches_username_and_name(client):
    c, S = client
    await _seed(S, [_acct(1, "1", "vitalik", name="vitalik.eth"),
                    _acct(2, "2", "vbuterin_fan", name="Fan Account"),
                    _acct(3, "3", "someoneelse", name="Vita Coco")])
    r = await c.get("/v1/search", params={"q": "vita"})
    names = [i["userName"] for i in r.json()["items"]]
    assert names[0] == "vitalik"               # rank order
    assert "someoneelse" in names              # name match counts too
    assert "vbuterin_fan" not in names


async def test_search_short_query_empty(client):
    c, _ = client
    r = await c.get("/v1/search", params={"q": "v"})
    assert r.json()["items"] == []
