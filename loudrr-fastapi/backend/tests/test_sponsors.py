"""Sponsored accounts — services/sponsors.py, the admin API, and the places a
sponsored post touches the rest of the app (feed ranking, expiry, settlement).

Nothing here talks to the gateway: the admin endpoints get a FakeGateway via
dependency override, and the poller gets one passed in directly.
"""
import calendar
import re
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import BadRequest
from app.core.time_utils import utcnow
from app.integrations import twitter
from app.integrations.x_stream import GatewayUnavailable, get_x_stream_client
from app.models.audit_log import AuditLog
from app.models.engagement import Engagement
from app.models.outbox_event import OutboxEvent, OutboxEventType
from app.models.post import Post
from app.models.sponsored_account import SponsoredAccount
from app.models.transaction import Transaction
from app.models.user import User
from app.models.verification_batch import VerificationBatch
from app.services import claims, feed, maintenance, sponsors
from app.services.sponsors import PLATFORM_USER_ID, SPONSOR_PLATFORM


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class FakeGateway:
    """Stands in for XStreamClient. `fail` names the methods that raise
    GatewayUnavailable; `pages` is what search_latest hands out, in order."""

    def __init__(self, *, configured=True, users=None, monitors=None, fail=(), pages=None):
        self.configured = configured
        self.users = users or {}
        self.monitors = list(monitors or [])
        self.fail = set(fail)
        self.pages = list(pages or [])
        self.calls: list[tuple] = []

    def _maybe_fail(self, name):
        if name in self.fail:
            raise GatewayUnavailable(f"{name}: gateway down")

    def websocket_url(self):
        return "wss://gateway.test/twitter/tweet/websocket"

    def headers(self):
        return {"x-api-key": "test-key"}

    async def user_info(self, username):
        self.calls.append(("user_info", username))
        self._maybe_fail("user_info")
        return self.users.get(username)

    async def add_monitor(self, username):
        self.calls.append(("add_monitor", username))
        self._maybe_fail("add_monitor")

    async def remove_monitor(self, username):
        self.calls.append(("remove_monitor", username))
        self._maybe_fail("remove_monitor")
        return True

    async def list_monitors(self):
        self.calls.append(("list_monitors",))
        self._maybe_fail("list_monitors")
        return [{"x_user_screen_name": name, "id_for_user": f"id-{name}"} for name in self.monitors]

    async def search_latest(self, query, cursor=""):
        self.calls.append(("search_latest", query, cursor))
        self._maybe_fail("search_latest")
        if not self.pages:
            return [], "", False
        return self.pages.pop(0)

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


def x_time(dt: datetime) -> str:
    """X's createdAt format for a naive-UTC datetime."""
    return dt.strftime("%a %b %d %H:%M:%S +0000 %Y")


def make_tweet(tweet_id, *, user="Acme", author_id="111", name="Acme Inc",
               avatar="https://pbs.twimg.com/acme.jpg", text="gm raiders",
               created=None, **extra):
    created = utcnow() - timedelta(minutes=1) if created is None else created
    return {
        "id": str(tweet_id), "text": text, "createdAt": x_time(created),
        "author": {"id": author_id, "userName": user, "name": name, "profilePicture": avatar},
        **extra,
    }


async def make_sponsor(db, handle="acme", **overrides) -> SponsoredAccount:
    values = dict(
        x_username=handle, x_user_id="111", display_name="Acme", avatar_url="",
        karma_per_post=Decimal("100"), active_since=utcnow() - timedelta(hours=1),
    )
    values.update(overrides)
    sponsor = SponsoredAccount(**values)
    db.add(sponsor)
    await db.commit()
    return sponsor


async def sponsored_posts(db) -> list[Post]:
    return (
        await db.execute(
            select(Post).where(Post.platform == SPONSOR_PLATFORM).order_by(Post.tweet_id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()


# ---------------------------------------------------------------------------
# 1. normalize_handle
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    "@Name", "name", "https://x.com/Name", "https://twitter.com/Name/status/1",
    " name ", "x.com/@Name", "https://x.com/Name?s=20", "  @NAME  ",
])
def test_normalize_handle_accepts(raw):
    assert sponsors.normalize_handle(raw) == "name"


@pytest.mark.parametrize("raw", [
    "", "   ", None, "a b", "abcdefghijklmnop", "x.com/", "@", "name!", "https://x.com/",
    "https://x.com/abcdefghijklmnopq",
])
def test_normalize_handle_rejects(raw):
    with pytest.raises(BadRequest):
        sponsors.normalize_handle(raw)


# ---------------------------------------------------------------------------
# 2. is_original_post
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tweet", [
    {"id": "1", "text": "hi", "isReply": True},
    {"id": "1", "text": "hi", "inReplyToId": "99"},
    {"id": "1", "text": "hi", "inReplyToUserId": "42"},
    {"id": "1", "text": "hi", "isRetweet": True},
    {"id": "1", "text": "hi", "retweeted_tweet": {"id": "7"}},
    {"id": "1", "text": "RT @someone: their post"},
])
def test_is_original_post_rejects_replies_and_retweets(tweet):
    assert sponsors.is_original_post(tweet) is False


@pytest.mark.parametrize("tweet", [
    {"id": "1", "text": "plain post"},
    {"id": "1", "text": "look at this", "quoted_tweet": {"id": "7", "text": "quoted"}},
    {"id": "1", "text": "hi", "isReply": False, "isRetweet": False,
     "inReplyToId": None, "retweeted_tweet": None},
    {"id": "1", "text": None},
    {"id": "1", "text": "mid-text RT @x is not a retweet"},
])
def test_is_original_post_accepts_plain_and_quote(tweet):
    assert sponsors.is_original_post(tweet) is True


# ---------------------------------------------------------------------------
# 3. tweets_in_frame
# ---------------------------------------------------------------------------
def test_tweets_in_frame_shapes():
    t1, t2 = {"id": "1", "text": "a"}, {"id": "2", "text": "b"}
    assert sponsors.tweets_in_frame({"event_type": "connected"}) == []
    assert sponsors.tweets_in_frame({"event_type": "ping", "tweets": [t1]}) == []
    assert sponsors.tweets_in_frame({"event_type": "tweet", "tweets": [t1, "junk", t2]}) == [t1, t2]
    assert sponsors.tweets_in_frame({"tweet": t1}) == [t1]
    assert sponsors.tweets_in_frame({"data": {"tweets": [t1, 3, t2]}}) == [t1, t2]
    assert sponsors.tweets_in_frame({"data": [t2]}) == [t2]
    bare = {"id": "5", "author": {"id": "111"}}
    assert sponsors.tweets_in_frame(bare) == [bare]


@pytest.mark.parametrize("frame", [
    None, "tweet", 42, [{"id": "1"}], {}, {"event_type": "tweet"},
    {"tweet": {"text": "no id"}}, {"data": {"foo": 1}}, {"tweets": "nope"},
    {"id": "5"},  # an id but no author dict — not a tweet
])
def test_tweets_in_frame_garbage(frame):
    assert sponsors.tweets_in_frame(frame) == []


# ---------------------------------------------------------------------------
# 4. parse_tweet_time
# ---------------------------------------------------------------------------
def test_parse_tweet_time_formats():
    expected = datetime(2026, 7, 28, 14, 30, 39)
    assert sponsors.parse_tweet_time("Tue Jul 28 14:30:39 +0000 2026") == expected
    assert sponsors.parse_tweet_time("2026-07-28T14:30:39Z") == expected
    assert sponsors.parse_tweet_time("2026-07-28T14:30:39+00:00") == expected
    # another offset is converted to UTC, and the result is always naive
    got = sponsors.parse_tweet_time("2026-07-28T16:30:39+02:00")
    assert got == expected and got.tzinfo is None
    got = sponsors.parse_tweet_time("Tue Jul 28 16:30:39 +0200 2026")
    assert got == expected and got.tzinfo is None


@pytest.mark.parametrize("value", [None, "", "yesterday", "Tue Jul 99 14:30:39 +0000 2026", 1753713039, {}])
def test_parse_tweet_time_junk(value):
    assert sponsors.parse_tweet_time(value) is None


# ---------------------------------------------------------------------------
# 5. ingest_tweet
# ---------------------------------------------------------------------------
async def test_ingest_tweet_creates_sponsored_post(db_session):
    sponsor = await make_sponsor(db_session, karma_per_post=Decimal("250"))
    created = (utcnow() - timedelta(minutes=5)).replace(microsecond=0)
    tweet = make_tweet(
        "1900000000000000001", created=created, text="big launch today",
        extendedEntities={"media": [
            {"media_url_https": "https://pbs.twimg.com/media/1.jpg"},
            {"url": "https://pbs.twimg.com/media/2.jpg"},
            "junk", {"type": "photo"},
        ]},
    )
    now = utcnow()

    post = await sponsors.ingest_tweet(db_session, tweet, now=now)
    await db_session.commit()

    assert post is not None
    assert post.is_sponsored is True
    assert post.platform == "sponsor"
    assert post.sponsor_id == sponsor.id
    assert post.user_id == PLATFORM_USER_ID
    assert post.status == "active"
    assert post.escrow == Decimal("250") and post.initial_escrow == Decimal("250")
    assert post.x_link == "https://x.com/Acme/status/1900000000000000001"
    assert post.tweet_id == "1900000000000000001"
    assert post.tweet_text == "big launch today"
    assert post.tweet_author_name == "Acme Inc"
    assert post.tweet_author_username == "Acme"
    assert post.tweet_author_avatar == "https://pbs.twimg.com/acme.jpg"
    assert post.tweet_media == [
        "https://pbs.twimg.com/media/1.jpg", "https://pbs.twimg.com/media/2.jpg",
    ]
    assert post.tweet_created_at == created

    await db_session.refresh(sponsor)
    assert sponsor.posts_created == 1
    assert sponsor.last_tweet_id == "1900000000000000001"
    assert sponsor.last_post_at == now
    # the author's current name/avatar are copied onto the account
    assert sponsor.display_name == "Acme Inc"
    assert sponsor.avatar_url == "https://pbs.twimg.com/acme.jpg"

    # the platform user owns it: no Telegram account (nobody can log in as
    # it), no balance
    platform = await db_session.get(User, PLATFORM_USER_ID)
    assert platform.telegram_id is None
    assert platform.credits == Decimal("0")


async def test_ingest_tweet_without_author_details_falls_back_to_sponsor(db_session):
    await make_sponsor(db_session, handle="acme", x_user_id="111",
                       display_name="Acme Corp", avatar_url="https://cdn/acme.png")
    tweet = {"id": "555", "text": "gm", "author": {"id": "111"}}  # no createdAt either

    post = await sponsors.ingest_tweet(db_session, tweet)
    await db_session.commit()

    assert post is not None
    assert post.x_link == "https://x.com/acme/status/555"
    assert post.tweet_author_username == "acme"
    assert post.tweet_author_name == "Acme Corp"
    assert post.tweet_author_avatar == "https://cdn/acme.png"
    assert post.tweet_created_at is None
    assert post.tweet_media == []


async def test_ingest_tweet_unknown_author(db_session):
    await make_sponsor(db_session)
    tweet = make_tweet("100", user="stranger", author_id="999")
    assert await sponsors.ingest_tweet(db_session, tweet) is None
    assert await sponsored_posts(db_session) == []


async def test_ingest_tweet_paused_sponsor(db_session):
    await make_sponsor(db_session, is_active=False)
    assert await sponsors.ingest_tweet(db_session, make_tweet("100")) is None


@pytest.mark.parametrize("extra", [
    {"isReply": True},
    {"inReplyToId": "42"},
    {"inReplyToUserId": "111"},  # a thread reply to itself is still a reply
    {"isRetweet": True},
    {"retweeted_tweet": {"id": "9"}},
    {"text": "RT @other: something"},
])
async def test_ingest_tweet_skips_replies_and_retweets(db_session, extra):
    sponsor = await make_sponsor(db_session)
    assert await sponsors.ingest_tweet(db_session, make_tweet("100", **extra)) is None
    await db_session.refresh(sponsor)
    assert sponsor.posts_created == 0


async def test_ingest_tweet_quote_post_counts(db_session):
    await make_sponsor(db_session)
    tweet = make_tweet("100", quoted_tweet={"id": "7", "text": "someone else"})
    assert await sponsors.ingest_tweet(db_session, tweet) is not None


async def test_ingest_tweet_active_since_with_grace(db_session):
    now = utcnow()
    await make_sponsor(db_session, active_since=now)
    # posted 3 minutes before the account was added — not imported
    too_early = make_tweet("100", created=now - timedelta(minutes=3))
    assert await sponsors.ingest_tweet(db_session, too_early, now=now) is None
    # within ACTIVE_SINCE_GRACE (2 min): X's clock vs ours — imported
    in_grace = make_tweet("101", created=now - timedelta(minutes=1))
    assert await sponsors.ingest_tweet(db_session, in_grace, now=now) is not None


async def test_ingest_tweet_max_age(db_session):
    now = utcnow()
    await make_sponsor(db_session, active_since=now - timedelta(days=3))
    stale = make_tweet("100", created=now - sponsors.MAX_TWEET_AGE - timedelta(minutes=5))
    assert await sponsors.ingest_tweet(db_session, stale, now=now) is None
    recent = make_tweet("101", created=now - sponsors.MAX_TWEET_AGE + timedelta(minutes=5))
    assert await sponsors.ingest_tweet(db_session, recent, now=now) is not None


@pytest.mark.parametrize("tweet_id", ["abc", "", None, "12a", " "])
async def test_ingest_tweet_non_numeric_id(db_session, tweet_id):
    await make_sponsor(db_session)
    tweet = make_tweet("1")
    tweet["id"] = tweet_id
    assert await sponsors.ingest_tweet(db_session, tweet) is None


async def test_ingest_tweet_not_a_dict(db_session):
    assert await sponsors.ingest_tweet(db_session, "tweet") is None
    assert await sponsors.ingest_tweet(db_session, None) is None


async def test_ingest_tweet_duplicate(db_session):
    sponsor = await make_sponsor(db_session)
    assert await sponsors.ingest_tweet(db_session, make_tweet("100")) is not None
    await db_session.commit()
    assert await sponsors.ingest_tweet(db_session, make_tweet("100")) is None
    await db_session.commit()
    assert len(await sponsored_posts(db_session)) == 1
    await db_session.refresh(sponsor)
    assert sponsor.posts_created == 1


async def test_ingest_tweet_matches_by_user_id_after_rename(db_session):
    sponsor = await make_sponsor(db_session, handle="acme", x_user_id="111")
    tweet = make_tweet("100", user="AcmeRebrand", author_id="111")
    post = await sponsors.ingest_tweet(db_session, tweet)
    assert post is not None
    assert post.sponsor_id == sponsor.id
    assert post.x_link == "https://x.com/AcmeRebrand/status/100"


async def test_ingest_tweet_user_id_beats_a_reused_handle(db_session):
    """@acme renamed away and someone else (also sponsored) took the handle:
    the permanent id decides whose post it is."""
    original = await make_sponsor(db_session, handle="oldacme", x_user_id="111")
    await make_sponsor(db_session, handle="acme", x_user_id="222")
    post = await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme", author_id="111"))
    assert post.sponsor_id == original.id


async def test_ingest_tweet_backfills_missing_user_id(db_session):
    sponsor = await make_sponsor(db_session, handle="acme", x_user_id="")
    post = await sponsors.ingest_tweet(db_session, make_tweet("100", user="ACME", author_id="777"))
    await db_session.commit()
    assert post is not None
    await db_session.refresh(sponsor)
    assert sponsor.x_user_id == "777"


async def test_platform_user_created_once(db_session):
    await make_sponsor(db_session)
    assert await sponsors.ingest_tweet(db_session, make_tweet("100")) is not None
    await db_session.commit()
    assert await sponsors.ingest_tweet(db_session, make_tweet("101")) is not None
    await db_session.commit()
    count = await db_session.scalar(
        select(func.count()).select_from(User).where(User.id == PLATFORM_USER_ID)
    )
    assert count == 1
    posts = await sponsored_posts(db_session)
    assert {p.user_id for p in posts} == {PLATFORM_USER_ID}


async def test_platform_user_created_concurrently(db_session, monkeypatch):
    """Another worker inserts the platform user between our lookup and our
    insert: the unique violation is absorbed and the existing row returned."""
    existing = await sponsors.platform_user(db_session)
    await db_session.commit()
    db_session.expunge(existing)
    real_get = db_session.get
    lookups = []

    async def _stale_first_lookup(model, ident, **kwargs):
        lookups.append(ident)
        if len(lookups) == 1:
            return None
        return await real_get(model, ident, **kwargs)

    monkeypatch.setattr(db_session, "get", _stale_first_lookup)
    user = await sponsors.platform_user(db_session)
    assert user is not None and user.id == PLATFORM_USER_ID
    assert len(lookups) == 2
    count = await db_session.scalar(select(func.count()).select_from(User).where(User.id == PLATFORM_USER_ID))
    assert count == 1


async def test_ingest_tweets_batch_commits_and_dedupes(db_session):
    sponsor = await make_sponsor(db_session)
    batch = [
        make_tweet("100"),
        make_tweet("100"),  # same tweet twice in one frame
        make_tweet("101", isReply=True),
        make_tweet("102"),
        "junk",
    ]
    created = await sponsors.ingest_tweets(db_session, batch)
    assert [p.tweet_id for p in created] == ["100", "102"]
    await db_session.rollback()  # proves ingest_tweets already committed
    assert [p.tweet_id for p in await sponsored_posts(db_session)] == ["100", "102"]
    await db_session.refresh(sponsor)
    assert sponsor.posts_created == 2
    assert sponsor.last_tweet_id == "102"


# ---------------------------------------------------------------------------
# 6. the partial unique index
# ---------------------------------------------------------------------------
async def test_unique_index_blocks_duplicate_sponsored_post(db_session, make_user):
    owner = await make_user()

    def _post(platform):
        return Post(
            user_id=owner.id, x_link="https://x.com/a/status/4242", tweet_id="4242",
            escrow=Decimal("10"), initial_escrow=Decimal("10"), platform=platform,
        )

    db_session.add(_post("sponsor"))
    await db_session.commit()

    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            db_session.add(_post("sponsor"))
    assert "uq_posts_sponsor_tweet" in str(exc.value)

    # a regular user submission of the same tweet is not affected
    db_session.add(_post("web"))
    await db_session.commit()
    db_session.add(_post("telegram"))
    await db_session.commit()
    total = await db_session.scalar(
        select(func.count()).select_from(Post).where(Post.tweet_id == "4242")
    )
    assert total == 3


# ---------------------------------------------------------------------------
# 7. search_queries
# ---------------------------------------------------------------------------
def test_search_queries_single_chunk():
    since = datetime(2026, 9, 17, 12, 30, 15)
    unix = calendar.timegm(since.timetuple())  # naive datetime read as UTC
    assert unix == 1789648215
    assert sponsors.search_queries(["a", "b"], since) == [
        f"(from:a OR from:b) -filter:replies -filter:retweets since_time:{unix}"
    ]
    assert sponsors.search_queries(["solo"], since) == [
        f"(from:solo) -filter:replies -filter:retweets since_time:{unix}"
    ]
    assert sponsors.search_queries([], since) == []


def test_search_queries_split_by_length():
    handles = [f"handle_{i:08d}" for i in range(120)]  # 15 chars each
    since = datetime(2026, 9, 17, 12, 0, 0)
    queries = sponsors.search_queries(handles, since)
    assert len(queries) > 1
    seen = []
    for q in queries:
        assert len(q) <= 450
        assert q.startswith("(") and q.endswith(
            f") -filter:replies -filter:retweets since_time:{calendar.timegm(since.timetuple())}"
        )
        seen.extend(re.findall(r"from:(\w+)", q))
    assert sorted(seen) == sorted(handles)
    assert len(seen) == len(set(seen))
    # order is kept (chunks are contiguous)
    assert seen == handles


# ---------------------------------------------------------------------------
# 8. poll_new_posts / poll_since
# ---------------------------------------------------------------------------
async def test_poll_new_posts_ingests_originals_in_id_order(db_session):
    acme = await make_sponsor(db_session, handle="acme", x_user_id="111")
    beta = await make_sponsor(db_session, handle="beta", x_user_id="222")
    await make_sponsor(db_session, handle="gamma", x_user_id="333", is_active=False)
    page = [
        make_tweet("300", user="acme", author_id="111"),
        make_tweet("310", user="acme", author_id="111", isReply=True),
        make_tweet("320", user="beta", author_id="222", isRetweet=True),
        make_tweet("200", user="acme", author_id="111"),
        make_tweet("250", user="beta", author_id="222"),
        make_tweet("400", user="gamma", author_id="333"),  # paused
    ]
    gw = FakeGateway(pages=[(page, "", False)])
    since = utcnow() - timedelta(minutes=10)

    created = await sponsors.poll_new_posts(db_session, gw, since=since)

    assert created == 3
    searches = gw.called("search_latest")
    assert len(searches) == 1
    assert searches[0][1] == sponsors.search_queries(["acme", "beta"], since)[0]
    assert searches[0][2] == ""
    assert [p.tweet_id for p in await sponsored_posts(db_session)] == ["200", "250", "300"]
    await db_session.refresh(acme)
    await db_session.refresh(beta)
    # oldest first, so the cursor fields end on the newest post
    assert acme.last_tweet_id == "300" and acme.posts_created == 2
    assert beta.last_tweet_id == "250" and beta.posts_created == 1


def _filler(n, start=1000):
    """n tweets from nobody we sponsor — a full page that imports nothing."""
    return [make_tweet(str(start + i), user="nobody", author_id="0") for i in range(n)]


async def test_poll_new_posts_follows_cursor_only_when_full(db_session):
    await make_sponsor(db_session)
    gw = FakeGateway(pages=[
        (_filler(15), "c1", True),
        (_filler(3, start=2000) + [make_tweet("5000")], "c2", True),  # short page
        (_filler(15, start=3000), "c3", True),  # never fetched
    ])
    created = await sponsors.poll_new_posts(db_session, gw, since=utcnow() - timedelta(minutes=5))
    assert created == 1
    assert [c[2] for c in gw.called("search_latest")] == ["", "c1"]


async def test_poll_new_posts_stops_without_next_page(db_session):
    await make_sponsor(db_session)
    gw = FakeGateway(pages=[(_filler(20), "c1", False), (_filler(15), "c2", True)])
    await sponsors.poll_new_posts(db_session, gw, since=utcnow())
    assert len(gw.called("search_latest")) == 1

    gw = FakeGateway(pages=[(_filler(20), "", True), (_filler(15), "c2", True)])
    await sponsors.poll_new_posts(db_session, gw, since=utcnow())
    assert len(gw.called("search_latest")) == 1


async def test_poll_new_posts_page_cap(db_session):
    await make_sponsor(db_session)
    pages = [(_filler(15, start=1000 * (i + 1)), f"c{i}", True) for i in range(10)]
    gw = FakeGateway(pages=pages)
    await sponsors.poll_new_posts(db_session, gw, since=utcnow())
    assert len(gw.called("search_latest")) == sponsors.MAX_POLL_PAGES


async def test_poll_new_posts_one_search_per_chunk(db_session, monkeypatch):
    await make_sponsor(db_session, handle="acme", x_user_id="111")
    await make_sponsor(db_session, handle="beta", x_user_id="222")
    monkeypatch.setattr(sponsors, "_QUERY_MAX_CHARS", 10)  # force one handle per query
    gw = FakeGateway(pages=[
        ([make_tweet("10", user="acme", author_id="111")], "", False),
        ([make_tweet("20", user="beta", author_id="222")], "", False),
    ])
    assert await sponsors.poll_new_posts(db_session, gw, since=utcnow()) == 2
    queries = [c[1] for c in gw.called("search_latest")]
    assert len(queries) == 2
    assert "from:acme" in queries[0] and "from:beta" in queries[1]


async def test_poll_new_posts_no_active_sponsors(db_session):
    await make_sponsor(db_session, is_active=False)
    gw = FakeGateway()
    assert await sponsors.poll_new_posts(db_session, gw, since=utcnow()) == 0
    assert gw.calls == []


async def test_poll_new_posts_gateway_unavailable_propagates(db_session):
    await make_sponsor(db_session)
    gw = FakeGateway(fail={"search_latest"})
    with pytest.raises(GatewayUnavailable):
        await sponsors.poll_new_posts(db_session, gw, since=utcnow())


async def test_poll_since_no_sponsors_is_now(db_session):
    got = await sponsors.poll_since(db_session)
    assert abs((utcnow() - got).total_seconds()) < 5


async def test_poll_since_oldest_active_window(db_session):
    now = utcnow()
    await make_sponsor(db_session, handle="a", x_user_id="1", active_since=now - timedelta(hours=3))
    # imported an hour ago: its window starts there, not at active_since
    await make_sponsor(db_session, handle="b", x_user_id="2", active_since=now - timedelta(hours=5),
                       last_post_at=now - timedelta(hours=1))
    # paused accounts don't widen the window
    await make_sponsor(db_session, handle="c", x_user_id="3", active_since=now - timedelta(hours=10),
                       is_active=False)
    got = await sponsors.poll_since(db_session)
    assert abs((got - (now - timedelta(hours=3))).total_seconds()) < 1


async def test_poll_since_clamped_to_max_tweet_age(db_session):
    await make_sponsor(db_session, active_since=utcnow() - timedelta(hours=30))
    got = await sponsors.poll_since(db_session)
    expected = utcnow() - sponsors.MAX_TWEET_AGE
    assert abs((got - expected).total_seconds()) < 5


# ---------------------------------------------------------------------------
# 9. sync_monitors
# ---------------------------------------------------------------------------
async def test_sync_monitors(db_session):
    await make_sponsor(db_session, handle="acme", x_user_id="1")            # active, missing
    await make_sponsor(db_session, handle="beta", x_user_id="2")            # active, monitored
    await make_sponsor(db_session, handle="gamma", x_user_id="3", is_active=False)  # paused, monitored
    await make_sponsor(db_session, handle="delta", x_user_id="4", is_active=False)  # paused, not
    gw = FakeGateway(monitors=["Beta", "gamma", "someone_else"])

    result = await sponsors.sync_monitors(db_session, gw)

    assert result == {"added": ["acme"], "removed": ["gamma"], "failed": []}
    assert gw.called("add_monitor") == [("add_monitor", "acme")]
    assert gw.called("remove_monitor") == [("remove_monitor", "gamma")]


async def test_sync_monitors_reads_x_user_name_rows(db_session):
    await make_sponsor(db_session, handle="acme")

    class _Gw(FakeGateway):
        async def list_monitors(self):
            return [{"x_user_name": "ACME"}]

    gw = _Gw()
    assert await sponsors.sync_monitors(db_session, gw) == {"added": [], "removed": [], "failed": []}


async def test_sync_monitors_gateway_down_propagates(db_session):
    await make_sponsor(db_session)
    with pytest.raises(GatewayUnavailable):
        await sponsors.sync_monitors(db_session, FakeGateway(fail={"list_monitors"}))


# ---------------------------------------------------------------------------
# 10. admin API
# ---------------------------------------------------------------------------
@pytest.fixture
def gateway(client):
    """A FakeGateway injected into the admin endpoints (cleared by `client`)."""
    from app.main import app

    fake = FakeGateway(users={
        "acmehq": {"id": "5001", "userName": "AcmeHQ", "name": "Acme HQ",
                   "profilePicture": "https://pbs.twimg.com/acmehq.jpg"},
        "locked": {"id": "5002", "userName": "Locked", "protected": True},
    })
    app.dependency_overrides[get_x_stream_client] = lambda: fake
    return fake


async def _admin(make_user):
    return await make_user(role="admin")


async def _sponsor_rows(db):
    return (
        await db.execute(select(SponsoredAccount).execution_options(populate_existing=True))
    ).scalars().all()


async def test_admin_sponsors_forbidden_for_non_admin(client, make_user, gateway, db_session):
    user = await make_user(role="")
    sponsor = await make_sponsor(db_session)
    params = {"telegram_id": user.telegram_id}
    assert (await client.get("/api/admin/sponsors/", params=params)).status_code == 403
    r = await client.post("/api/admin/sponsors/", params=params, json={"x_username": "acmehq"})
    assert r.status_code == 403
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params=params, json={"is_active": False})
    assert r.status_code == 403
    assert (await client.delete(f"/api/admin/sponsors/{sponsor.id}/", params=params)).status_code == 403
    assert (await client.get("/api/admin/sponsors/")).status_code == 401
    assert gateway.calls == []


async def test_admin_add_sponsor(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    r = await client.post(
        "/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
        json={"x_username": "https://x.com/AcmeHQ", "karma_per_post": 250, "notes": "  launch deal "},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["x_username"] == "acmehq"
    assert body["x_user_id"] == "5001"
    assert body["display_name"] == "Acme HQ"
    assert body["avatar_url"] == "https://pbs.twimg.com/acmehq.jpg"
    assert body["is_active"] is True
    assert body["karma_per_post"] == 250.0
    assert body["notes"] == "launch deal"
    assert body["posts_created"] == 0 and body["posts_total"] == 0

    assert gateway.calls == [("user_info", "acmehq"), ("add_monitor", "acmehq")]
    [row] = await _sponsor_rows(db_session)
    assert row.x_username == "acmehq"
    assert row.karma_per_post == Decimal("250")
    assert row.created_by_id == admin.id
    assert abs((utcnow() - row.active_since).total_seconds()) < 10

    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "add_sponsor"))).scalar_one()
    assert log.actor_id == admin.id
    assert log.target_type == "sponsored_account"
    assert log.target_id == row.id
    assert log.detail["x_username"] == "acmehq"
    assert log.detail["karma_per_post"] == "250"


async def test_admin_add_sponsor_default_karma(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "@acmehq"})
    assert r.status_code == 200, r.text
    assert r.json()["karma_per_post"] == float(sponsors.DEFAULT_KARMA_PER_POST)


async def test_admin_add_sponsor_duplicate_409(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    params = {"telegram_id": admin.telegram_id}
    assert (await client.post("/api/admin/sponsors/", params=params,
                              json={"x_username": "acmehq"})).status_code == 200
    gateway.calls.clear()
    r = await client.post("/api/admin/sponsors/", params=params, json={"x_username": "@ACMEHQ"})
    assert r.status_code == 409
    assert "already" in r.json()["error"]
    assert gateway.calls == []  # rejected before touching the gateway
    assert len(await _sponsor_rows(db_session)) == 1


async def test_admin_add_sponsor_duplicate_paused_hint(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    await make_sponsor(db_session, handle="acmehq", is_active=False)
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "acmehq"})
    assert r.status_code == 409
    assert "resume" in r.json()["error"]


async def test_admin_add_sponsor_unknown_handle_400(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "ghost"})
    assert r.status_code == 400
    assert "doesn't exist" in r.json()["error"]
    assert gateway.called("add_monitor") == []
    assert await _sponsor_rows(db_session) == []


async def test_admin_add_sponsor_protected_400(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "locked"})
    assert r.status_code == 400
    assert "protected" in r.json()["error"]
    assert gateway.called("add_monitor") == []
    assert await _sponsor_rows(db_session) == []


async def test_admin_add_sponsor_gateway_down_503(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    gateway.fail.add("user_info")
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "acmehq"})
    assert r.status_code == 503
    assert await _sponsor_rows(db_session) == []
    assert (await db_session.execute(select(AuditLog))).scalars().all() == []


async def test_admin_add_sponsor_gateway_not_configured_503(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    gateway.configured = False
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "acmehq"})
    assert r.status_code == 503
    assert "LOUDRR_GATEWAY_API" in r.json()["error"]
    assert gateway.calls == []
    assert await _sponsor_rows(db_session) == []


@pytest.mark.parametrize("karma, status", [
    (0, 400), (-5, 400), (100001, 400), ("0.5", 400), ("abc", 422), (None, 422),
])
async def test_admin_add_sponsor_invalid_karma(client, make_user, gateway, db_session, karma, status):
    admin = await _admin(make_user)
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "acmehq", "karma_per_post": karma})
    assert r.status_code == status, r.text
    assert gateway.calls == []
    assert await _sponsor_rows(db_session) == []


@pytest.mark.parametrize("handle", ["a b", "way_too_long_handle_x", "x.com/", "!!!"])
async def test_admin_add_sponsor_invalid_handle_400(client, make_user, gateway, db_session, handle):
    admin = await _admin(make_user)
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": handle})
    assert r.status_code == 400
    assert gateway.calls == []


async def test_admin_list_sponsors_stats(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    older = await make_sponsor(db_session, handle="acme", x_user_id="111",
                               created_at=utcnow() - timedelta(days=2))
    newer = await make_sponsor(db_session, handle="beta", x_user_id="222")

    # acme: one live post (3 karma paid out, one engagement still pending)
    # and one EXPIRED post (2 paid before expiry; its leftover escrow lapsed)
    live = await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme", author_id="111"))
    expired = await sponsors.ingest_tweet(db_session, make_tweet("101", user="acme", author_id="111"))
    await db_session.commit()
    live.escrow = Decimal("97")
    expired.escrow = Decimal("98")
    await db_session.commit()
    u1, u2, u3 = await make_user(), await make_user(), await make_user()
    db_session.add_all([
        Engagement(user_id=u1.id, post_id=live.id, verified=True, credit_granted=True,
                   verification_data={"result": "awarded", "amount": "3.0000"}),
        Engagement(user_id=u2.id, post_id=live.id),  # clicked, not settled yet
        Engagement(user_id=u3.id, post_id=expired.id, verified=True, credit_granted=True,
                   verification_data={"result": "awarded", "amount": "2.0000"}),
    ])
    await db_session.commit()
    expired.created_at = utcnow() - timedelta(hours=60)
    await db_session.commit()
    assert await maintenance.expire_old_posts(db_session) == 1

    # a regular post with engagements doesn't leak into any sponsor's stats
    owner = await make_user()
    web = Post(user_id=owner.id, x_link="https://x.com/o/status/9", tweet_id="9",
               escrow=Decimal("40"), initial_escrow=Decimal("50"), platform="web")
    db_session.add(web)
    await db_session.commit()
    db_session.add(Engagement(user_id=u1.id, post_id=web.id, verified=True, credit_granted=True,
                              verification_data={"result": "awarded", "amount": "10"}))
    await db_session.commit()

    r = await client.get("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["x_username"] for row in rows] == ["beta", "acme"]  # newest first
    beta, acme = rows
    assert acme["id"] == str(older.id)
    assert acme["posts_created"] == 2
    assert acme["posts_total"] == 2
    assert acme["active_posts"] == 1
    assert acme["engagements"] == 3
    # what engagers actually earned — NOT initial - escrow, which would count
    # the expired post's lapsed 98 as paid
    assert acme["karma_paid"] == 5.0
    assert acme["last_tweet_id"] == "101"
    assert acme["last_post_at"] is not None

    assert beta["id"] == str(newer.id)
    assert (beta["posts_total"], beta["active_posts"], beta["engagements"], beta["karma_paid"]) == (0, 0, 0, 0.0)
    assert beta["last_tweet_id"] is None and beta["last_post_at"] is None
    assert gateway.calls == []


async def test_admin_list_sponsors_empty(client, make_user, gateway):
    admin = await _admin(make_user)
    r = await client.get("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id})
    assert r.status_code == 200
    assert r.json() == []


async def test_admin_pause_sponsor(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme")
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"is_active": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    assert gateway.calls == [("remove_monitor", "acme")]
    [row] = await _sponsor_rows(db_session)
    assert row.is_active is False
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "update_sponsor"))).scalar_one()
    assert log.detail == {"x_username": "acme", "is_active": False}


async def test_admin_pause_sponsor_gateway_down_still_pauses(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme")
    gateway.fail.add("remove_monitor")
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"is_active": False})
    assert r.status_code == 200, r.text
    assert gateway.called("remove_monitor") == [("remove_monitor", "acme")]
    [row] = await _sponsor_rows(db_session)
    assert row.is_active is False
    # and a paused account's posts are no longer imported
    assert await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme")) is None


async def test_admin_resume_sponsor(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    paused_at = utcnow() - timedelta(days=5)
    sponsor = await make_sponsor(db_session, handle="acme", is_active=False, active_since=paused_at)
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"is_active": True})
    assert r.status_code == 200, r.text
    assert gateway.calls == [("add_monitor", "acme")]
    [row] = await _sponsor_rows(db_session)
    assert row.is_active is True
    assert row.active_since > paused_at
    assert abs((utcnow() - row.active_since).total_seconds()) < 10
    # a post made while it was paused is not imported after the resume
    during_pause = make_tweet("100", user="acme", created=utcnow() - timedelta(hours=1))
    assert await sponsors.ingest_tweet(db_session, during_pause) is None


async def test_admin_resume_sponsor_gateway_down_still_resumes(client, make_user, gateway, db_session):
    """The poll doesn't need the gateway monitor, so a failing monitor call
    never blocks a resume (the stream's periodic sync re-adds it)."""
    admin = await _admin(make_user)
    paused_at = utcnow() - timedelta(days=5)
    sponsor = await make_sponsor(db_session, handle="acme", is_active=False, active_since=paused_at)
    gateway.fail.add("add_monitor")
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"is_active": True})
    assert r.status_code == 200, r.text
    assert gateway.called("add_monitor") == [("add_monitor", "acme")]
    [row] = await _sponsor_rows(db_session)
    assert row.is_active is True
    assert row.active_since > paused_at


async def test_admin_update_sponsor_karma_and_notes(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme", notes="")
    live = await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme"))
    await db_session.commit()
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"karma_per_post": "300", "notes": "  vip  "})
    assert r.status_code == 200, r.text
    assert r.json()["karma_per_post"] == 300.0
    assert r.json()["notes"] == "vip"
    assert gateway.calls == []
    [row] = await _sponsor_rows(db_session)
    assert row.karma_per_post == Decimal("300") and row.notes == "vip" and row.is_active is True
    # applies to future posts only
    await db_session.refresh(live)
    assert live.initial_escrow == Decimal("100")
    nxt = await sponsors.ingest_tweet(db_session, make_tweet("101", user="acme"))
    assert nxt.initial_escrow == Decimal("300")
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "update_sponsor"))).scalar_one()
    assert log.detail == {"x_username": "acme", "karma_per_post": "300", "notes": True}


async def test_admin_update_sponsor_no_changes_no_audit(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme", notes="vip")
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"is_active": True, "karma_per_post": 100, "notes": "vip"})
    assert r.status_code == 200, r.text
    assert gateway.calls == []  # already active: no monitor call
    assert (await db_session.execute(select(AuditLog))).scalars().all() == []


@pytest.mark.parametrize("karma, status", [(0, 400), (-5, 400), (100001, 400), ("abc", 422)])
async def test_admin_update_sponsor_invalid_karma(client, make_user, gateway, db_session, karma, status):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme")
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"karma_per_post": karma})
    assert r.status_code == status, r.text
    await db_session.rollback()
    [row] = await _sponsor_rows(db_session)
    assert row.karma_per_post == Decimal("100")


async def test_admin_delete_sponsor_keeps_posts(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme")
    post = await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme"))
    await db_session.commit()
    sponsor_id = sponsor.id

    r = await client.delete(f"/api/admin/sponsors/{sponsor_id}/", params={"telegram_id": admin.telegram_id})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert gateway.calls == [("remove_monitor", "acme")]
    assert await _sponsor_rows(db_session) == []

    await db_session.refresh(post)
    assert post.sponsor_id is None
    assert post.is_sponsored is True
    assert post.platform == "sponsor"
    assert post.status == "active"
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "delete_sponsor"))).scalar_one()
    assert log.target_id == sponsor_id and log.detail == {"x_username": "acme"}

    # the orphaned post still renders as the X account's post
    viewer = await make_user()
    card = await feed.format_post(db_session, post, viewer)
    assert card["creator_x_username"] == "acme" and card["is_sponsored"] is True


async def test_admin_delete_sponsor_gateway_down_503(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme")
    gateway.fail.add("remove_monitor")
    r = await client.delete(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id})
    assert r.status_code == 503
    await db_session.rollback()
    assert len(await _sponsor_rows(db_session)) == 1
    assert (await db_session.execute(select(AuditLog))).scalars().all() == []


async def test_admin_delete_sponsor_gateway_not_configured(client, make_user, gateway, db_session):
    """With no gateway key there's no monitor to remove — the row just goes."""
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme")
    gateway.configured = False
    r = await client.delete(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id})
    assert r.status_code == 200
    assert gateway.calls == []
    assert await _sponsor_rows(db_session) == []


async def test_admin_sponsor_unknown_id_404(client, make_user, gateway):
    import uuid

    admin = await _admin(make_user)
    params = {"telegram_id": admin.telegram_id}
    missing = uuid.uuid4()
    r = await client.patch(f"/api/admin/sponsors/{missing}/", params=params, json={"is_active": False})
    assert r.status_code == 404
    assert (await client.delete(f"/api/admin/sponsors/{missing}/", params=params)).status_code == 404
    assert (await client.patch("/api/admin/sponsors/not-a-uuid/", params=params, json={})).status_code == 422
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# 11. feed
# ---------------------------------------------------------------------------
async def _ranking_setup(db_session, make_user):
    viewer = await make_user(tweetscout_score=0, x_username="viewer")
    star = await make_user(tweetscout_score=5000, display_name="Star")
    normal = Post(user_id=star.id, x_link="https://x.com/star/status/1", tweet_id="1",
                  escrow=Decimal("50"), initial_escrow=Decimal("50"), platform="web")
    db_session.add(normal)
    await db_session.commit()
    await make_sponsor(db_session, handle="acme")
    sponsored = await sponsors.ingest_tweet(db_session, make_tweet("100"))  # author @Acme
    await db_session.commit()
    # make the sponsored post score far worse: old and nearly drained
    sponsored.created_at = utcnow() - timedelta(hours=40)
    sponsored.escrow = Decimal("10")
    await db_session.commit()
    return viewer, normal, sponsored


async def test_feed_sponsored_post_ranks_first(db_session, make_user):
    viewer, normal, sponsored = await _ranking_setup(db_session, make_user)

    posts = await feed.get_feed_posts(db_session, viewer)

    assert [p.id for p in posts] == [sponsored.id, normal.id]
    # ...even though it scores lower
    assert feed.calculate_feed_score(sponsored, viewer) < feed.calculate_feed_score(normal, viewer)
    assert await feed.get_feed_count(db_session, viewer) == 2


async def test_format_post_sponsored_creator_is_the_x_account(db_session, make_user):
    viewer, _normal, sponsored = await _ranking_setup(db_session, make_user)
    assert viewer.id != PLATFORM_USER_ID

    card = await feed.format_post(db_session, sponsored, viewer)

    assert card["creator"] == "Acme Inc"
    assert card["creator_x_username"] == "Acme"
    assert card["creator_avatar"] == "https://pbs.twimg.com/acme.jpg"
    assert card["is_sponsored"] is True
    assert card["tweet_id"] == "100"
    assert card["escrow_remaining"] == 10.0
    assert card["x_link"] == "https://x.com/Acme/status/100"


async def test_session_start_lists_sponsored_first(client, db_session, make_user):
    viewer, normal, sponsored = await _ranking_setup(db_session, make_user)
    r = await client.post("/session/start/", params={"telegram_id": viewer.telegram_id})
    assert r.status_code == 200, r.text
    cards = r.json()["posts"]
    assert [c["id"] for c in cards] == [str(sponsored.id), str(normal.id)]
    assert cards[0]["is_sponsored"] is True and cards[0]["creator"] == "Acme Inc"
    assert cards[1]["is_sponsored"] is False and cards[1]["creator"] == "Star"


# ---------------------------------------------------------------------------
# 12. expiry
# ---------------------------------------------------------------------------
async def test_expire_sponsored_post_no_refund_no_notice(db_session, make_user):
    await make_sponsor(db_session, handle="acme")
    sponsored = await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme"))
    await db_session.commit()
    sponsored.created_at = utcnow() - timedelta(hours=50)
    owner = await make_user(credits=Decimal("0"))
    normal = Post(user_id=owner.id, x_link="https://x.com/o/status/1", tweet_id="1",
                  escrow=Decimal("50"), initial_escrow=Decimal("50"), platform="web",
                  created_at=utcnow() - timedelta(hours=50))
    db_session.add(normal)
    await db_session.commit()

    assert await maintenance.expire_old_posts(db_session) == 2

    await db_session.refresh(sponsored)
    assert sponsored.status == "cancelled" and sponsored.escrow == Decimal("0")
    platform = await db_session.get(User, PLATFORM_USER_ID)
    await db_session.refresh(platform)
    assert platform.credits == Decimal("0")
    assert platform.total_credits_earned == Decimal("0")
    platform_txns = await db_session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.user_id == PLATFORM_USER_ID)
    )
    assert platform_txns == 0

    # the normal post in the same run is still refunded + notified
    await db_session.refresh(normal)
    await db_session.refresh(owner)
    assert normal.status == "cancelled"
    assert owner.credits == Decimal("50")
    events = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == OutboxEventType.POST_EXPIRED.value)
        )
    ).scalars().all()
    assert [e.payload["post_id"] for e in events] == [str(normal.id)]


# ---------------------------------------------------------------------------
# 13. settlement smoke
# ---------------------------------------------------------------------------
class _PassTwitter:
    async def verify_reply(self, *args, **kwargs):
        return {"passed": True, "reply_verified": True, "like_verified": True,
                "error": None, "skipped": False}


async def test_settling_sponsored_post_pays_from_escrow_and_awards_xp(db_session, make_user, monkeypatch):
    await make_sponsor(db_session, handle="acme")
    post = await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme"))
    await db_session.commit()
    viewer = await make_user(x_username="viewer")
    eng = Engagement(user_id=viewer.id, post_id=post.id, clicked_at=utcnow())
    db_session.add(eng)
    await db_session.commit()
    batch = VerificationBatch(user_id=viewer.id, engagement_ids=[str(eng.id)], status="pending")
    db_session.add(batch)
    await db_session.commit()
    monkeypatch.setattr(twitter, "get_twitter_client", lambda: _PassTwitter())

    await claims.run_batch(db_session, batch.id)

    await db_session.refresh(post)
    await db_session.refresh(viewer)
    await db_session.refresh(eng)
    assert eng.credit_granted is True
    assert post.escrow == Decimal("99")  # karma came out of the sponsored escrow
    assert viewer.credits == Decimal("1")
    assert viewer.sponsored_xp == 5  # SPONSORED_XP_PER_ENGAGEMENT default
    assert viewer.sponsored_engagements == 1
    platform = await db_session.get(User, PLATFORM_USER_ID)
    await db_session.refresh(platform)
    assert platform.credits == Decimal("0")

    [stats] = await sponsors.list_sponsors(db_session)
    assert stats["engagements"] == 1
    assert stats["karma_paid"] == 1.0
    assert stats["active_posts"] == 1


# ---------------------------------------------------------------------------
# follow-ups: full row on PATCH, renamed accounts, tighter restart window,
# race-free posts_created
# ---------------------------------------------------------------------------
async def test_admin_patch_returns_row_with_stats(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    sponsor = await make_sponsor(db_session, handle="acme")
    await sponsors.ingest_tweets(db_session, [make_tweet("100", user="acme")])
    r = await client.patch(f"/api/admin/sponsors/{sponsor.id}/", params={"telegram_id": admin.telegram_id},
                           json={"karma_per_post": 40})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["karma_per_post"] == 40.0
    assert body["posts_total"] == 1 and body["active_posts"] == 1 and body["posts_created"] == 1


async def test_admin_add_renamed_account_repoints_the_sponsor(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    # @acmehq's X id 5001 is already a sponsor under its old handle
    old = await make_sponsor(db_session, handle="oldacme", x_user_id="5001", karma_per_post=Decimal("70"),
                             notes="deal A")
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "acmehq", "karma_per_post": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(old.id)
    assert body["x_username"] == "acmehq"
    assert body["karma_per_post"] == 70.0 and body["notes"] == "deal A"  # settings kept
    assert body["display_name"] == "Acme HQ"
    [row] = await _sponsor_rows(db_session)
    assert row.x_username == "acmehq"
    assert gateway.called("add_monitor") == [("add_monitor", "acmehq")]
    assert gateway.called("remove_monitor") == [("remove_monitor", "oldacme")]
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "rename_sponsor"))).scalar_one()
    assert log.actor_id == admin.id
    assert log.detail == {"x_username": "acmehq", "old_x_username": "oldacme"}


async def test_poll_since_resumed_sponsor_ignores_older_import(db_session):
    now = utcnow()
    # imported 8h ago, paused, resumed 10 minutes ago: nothing before the resume
    # is importable, so the restart poll must not reach back 8 hours
    await make_sponsor(db_session, active_since=now - timedelta(minutes=10),
                       last_post_at=now - timedelta(hours=8))
    got = await sponsors.poll_since(db_session)
    assert abs((got - (now - timedelta(minutes=10))).total_seconds()) < 1


async def test_ingest_counts_posts_in_sql_not_from_a_stale_object(db_session):
    from sqlalchemy import text

    sponsor = await make_sponsor(db_session)
    assert sponsor.posts_created == 0
    # another session (the push loop) imported 5 posts meanwhile; this
    # session's loaded object still says 0
    await db_session.execute(text("UPDATE sponsored_accounts SET posts_created = 5"))
    await sponsors.ingest_tweets(db_session, [make_tweet("100")])
    stored = await db_session.scalar(text("SELECT posts_created FROM sponsored_accounts"))
    assert stored == 6



# ---------------------------------------------------------------------------
# review fixes: handle squatting, renames, reserved links, batch isolation
# ---------------------------------------------------------------------------
async def test_ingest_ignores_a_different_account_on_the_sponsors_old_handle(db_session):
    """@acme (id 111) renamed; a stranger (id 999) registered @acme."""
    await make_sponsor(db_session, handle="acme", x_user_id="111")
    squatter = make_tweet("100", user="acme", author_id="999")
    assert await sponsors.ingest_tweet(db_session, squatter) is None
    assert await sponsored_posts(db_session) == []


async def test_ingest_handle_match_still_works_without_ids(db_session):
    # a sponsor whose X id was never stored, or a tweet without an author id
    await make_sponsor(db_session, handle="acme", x_user_id="")
    assert await sponsors.ingest_tweet(db_session, make_tweet("100", user="acme", author_id="999")) is not None
    await make_sponsor(db_session, handle="beta", x_user_id="222")
    assert await sponsors.ingest_tweet(db_session, make_tweet("101", user="beta", author_id="")) is not None


@pytest.mark.parametrize("raw", [
    "https://x.com/i/web/status/1790000000000000000",
    "https://x.com/home",
    "https://twitter.com/search?q=loudrr",
    "https://x.com/intent/follow",
    "x.com/explore",
])
def test_normalize_handle_rejects_x_pages(raw):
    with pytest.raises(BadRequest):
        sponsors.normalize_handle(raw)


def test_normalize_handle_intent_link_uses_screen_name():
    assert sponsors.normalize_handle("https://x.com/intent/user?screen_name=AcmeHQ") == "acmehq"
    assert sponsors.normalize_handle("https://twitter.com/intent/follow?lang=en&screen_name=@Acme_1") == "acme_1"


async def test_refresh_handles_follows_a_rename(db_session):
    await make_sponsor(db_session, handle="oldacme", x_user_id="111")
    await make_sponsor(db_session, handle="steady", x_user_id="222")
    await make_sponsor(db_session, handle="paused", x_user_id="333", is_active=False)

    class _Gw(FakeGateway):
        async def users_by_ids(self, ids):
            self.calls.append(("users_by_ids", tuple(ids)))
            return [{"id": "111", "userName": "AcmeNew", "name": "Acme New", "profilePicture": "https://a/n.jpg"},
                    {"id": "222", "userName": "Steady"}]

    gw = _Gw()
    assert await sponsors.refresh_handles(db_session, gw) == [("oldacme", "acmenew")]
    [(_, ids)] = gw.called("users_by_ids")
    assert sorted(ids) == ["111", "222"]  # paused sponsors aren't looked up
    rows = {r.x_user_id: r for r in await _sponsor_rows(db_session)}
    assert rows["111"].x_username == "acmenew" and rows["111"].display_name == "Acme New"
    assert rows["222"].x_username == "steady"
    assert gw.called("add_monitor") == [("add_monitor", "acmenew")]
    assert gw.called("remove_monitor") == [("remove_monitor", "oldacme")]
    # the poll now searches the new handle
    assert "from:acmenew" in sponsors.search_queries(["acmenew"], utcnow())[0]


async def test_refresh_handles_skips_a_handle_another_sponsor_uses(db_session):
    await make_sponsor(db_session, handle="oldacme", x_user_id="111")
    await make_sponsor(db_session, handle="taken", x_user_id="222")

    class _Gw(FakeGateway):
        async def users_by_ids(self, ids):
            return [{"id": "111", "userName": "taken"}, {"id": "222", "userName": "taken"}]

    gw = _Gw()
    assert await sponsors.refresh_handles(db_session, gw) == []
    assert sorted(r.x_username for r in await _sponsor_rows(db_session)) == ["oldacme", "taken"]
    assert gw.called("add_monitor") == []


async def test_refresh_handles_no_sponsors_no_call(db_session):
    class _Gw(FakeGateway):
        async def users_by_ids(self, ids):
            raise AssertionError("no lookup without sponsors")

    assert await sponsors.refresh_handles(db_session, _Gw()) == []


async def test_ingest_tweets_skips_a_tweet_that_errors(db_session, monkeypatch):
    await make_sponsor(db_session)
    real = sponsors.ingest_tweet

    async def _flaky(db, tweet, **kwargs):
        if tweet.get("id") == "101":
            raise RuntimeError("DataError: invalid byte sequence")
        return await real(db, tweet, **kwargs)

    monkeypatch.setattr(sponsors, "ingest_tweet", _flaky)
    created = await sponsors.ingest_tweets(db_session, [make_tweet("100"), make_tweet("101"), make_tweet("102")])
    assert [p.tweet_id for p in created] == ["100", "102"]
    await db_session.rollback()
    assert [p.tweet_id for p in await sponsored_posts(db_session)] == ["100", "102"]


async def test_sync_monitors_continues_after_one_failure(db_session):
    await make_sponsor(db_session, handle="alpha", x_user_id="1")
    await make_sponsor(db_session, handle="bravo", x_user_id="2")

    class _Gw(FakeGateway):
        async def add_monitor(self, username):
            self.calls.append(("add_monitor", username))
            if username == "alpha":
                raise GatewayUnavailable("monitor limit reached")

    gw = _Gw()
    result = await sponsors.sync_monitors(db_session, gw)
    assert result == {"added": ["bravo"], "removed": [], "failed": ["alpha"]}


async def test_admin_add_sponsor_monitor_failure_still_adds(client, make_user, gateway, db_session):
    admin = await _admin(make_user)
    gateway.fail.add("add_monitor")
    r = await client.post("/api/admin/sponsors/", params={"telegram_id": admin.telegram_id},
                          json={"x_username": "acmehq"})
    assert r.status_code == 200, r.text
    assert [s.x_username for s in await _sponsor_rows(db_session)] == ["acmehq"]
