"""Quick Reply drafts: cleaning, parsing, per-viewer variety, model routing
and fallback, storage, and the two places that ask for drafts."""
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.models.post import Post
from app.models.site_setting import SiteSetting
from app.models.x_profile import XProfile
from app.services import feed, quick_replies as qr
from app.services import posts as posts_svc
from app.services import site_settings, sponsors

CORPUS = [
    {"text": "the chart looks cooked ngl", "author": "a", "likes": 40,
     "parent": {"text": "solana chart update, new highs", "author": "x"}},
    {"text": "wen mainnet", "author": "b", "likes": 900, "parent": {"text": "testnet is live", "author": "y"}},
    {"text": "absolute shit take", "author": "c", "likes": 5, "parent": {"text": "eth is dead", "author": "z"}},
]
PROFILE = {"chars": {"median": 38, "p75": 70}, "starts_lowercase_pct": 100.0, "emoji_pct": 8.0}
CROWD = [
    {"text": "oh this is actually clean ngl", "author": "anon1", "likes": 12,
     "parent": {"text": "solana chart update, new highs", "author": "x"}},
    {"text": "u really shipped this in a week?", "author": "anon2", "likes": 3,
     "parent": {"text": "we shipped the new app", "author": "y"}},
]
# casual typing: always (1.0) so tests are deterministic; lowercase start never
CROWD_PROFILE = {"most_liked": {"starts_lowercase_pct": 0.0},
                 "shortforms": {"rates": {"u": 1.0, "dont": 1.0, "i": 1.0}, "extras": {"lol": 5.0, "oh": 2.0}}}
GROK = [{"question": "@grok is this true?", "score": 9.0,
         "parent": {"text": "solana just flipped eth in daily fees", "author": "x"}}]


@pytest.fixture(autouse=True)
def _study_data(monkeypatch):
    qr.corpus.cache_clear()
    qr.study.cache_clear()
    qr.crowd.cache_clear()
    qr.grok_questions.cache_clear()
    monkeypatch.setattr(qr, "corpus", lambda: CORPUS)
    monkeypatch.setattr(qr, "crowd", lambda: CROWD)
    monkeypatch.setattr(qr, "grok_questions", lambda: GROK)
    monkeypatch.setattr(qr, "profile", lambda: PROFILE)
    monkeypatch.setattr(qr, "crowd_profile", lambda: CROWD_PROFILE)
    monkeypatch.setattr(settings, "openrouter_api", "test-key")
    monkeypatch.setattr(settings, "quick_reply_creator_models", "free/one:free,free/two:free")
    monkeypatch.setattr(settings, "quick_reply_sponsored_models", "better/one,better/two")


def _post(**kw) -> Post:
    values = dict(id=uuid.uuid4(), user_id=uuid.uuid4(), x_link="https://x.com/sol/status/1", tweet_id="1",
                  tweet_text="solana just hit new highs, fees still tiny", tweet_author_username="solana",
                  escrow=Decimal("100"), initial_escrow=Decimal("100"), status="active", platform="sponsor",
                  is_sponsored=True, quick_replies=[])
    values.update(kw)
    return Post(**values)


def _answer(replies) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"replies": replies})}}]}


GOOD = ["fees this low should be illegal", "what's driving the volume",
        "sol summer never ended lol", "chart is going vertical", "cheap fees are the real unlock", "wen $1k"]


class FakeRouter:
    """OpenRouter stand-in: answers per model from a script."""

    def __init__(self, script: dict):
        self.script, self.models = script, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.models.append(body["model"])
        assert request.headers["authorization"] == "Bearer test-key"
        status, payload = self.script[body["model"]]
        return httpx.Response(status, json=payload)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


# ---- cleaning ----
@pytest.mark.parametrize("raw,expected", [
    ("wild move — respect", "wild move, respect"),
    ("this is it.", "this is it"),
    ("wait...", "wait..."),
    ("@solana @base lfg!!!", "lfg!"),
    ('"quoted reply"', "quoted reply"),
    ("one emoji \U0001F525", "one emoji \U0001F525"),
])
def test_clean_fixes_what_can_be_fixed(raw, expected):
    assert qr.clean(raw) == expected


def test_clean_keeps_the_first_sentence_of_a_long_draft():
    long = "MVP focus is the right call. Teams need shipping time, not another panel about tokenization"
    assert qr.clean(long) == "MVP focus is the right call"


@pytest.mark.parametrize("raw", [
    "Great point! Love this", "check https://x.com/a", "so bullish #SOL", "two \U0001F525\U0001F680",
    "this is total shit", "x", "",
    "a very long reply that goes on and on well past what any real top creator would ever type on a phone",
])
def test_clean_drops_what_cannot(raw):
    assert qr.clean(raw) is None


@pytest.mark.parametrize("content", [
    '{"replies": ["a b", "c d"]}',
    'sure! ```json\n{"replies": ["a b", "c d"]}\n```',
    '["a b", "c d"]',
])
def test_parse_reads_json_even_when_wrapped(content):
    assert qr.parse(content) == ["a b", "c d"]


def test_parse_garbage_is_empty():
    assert qr.parse("no json here") == [] and qr.parse("") == []


def test_finalize_drops_duplicates_and_copies_of_real_replies():
    rng = __import__("random").Random(1)
    out = qr.finalize(["Same thing", "same thing!", "The chart looks cooked ngl", "wen mainnet", "Fresh take"],
                      rng=rng)
    # a real creator's own line is dropped, a generic short one isn't; drafts
    # are stored as written (casing is per viewer, see humanize)
    assert out == ["Same thing", "wen mainnet", "Fresh take"]


# ---- viewers ----
def test_humanize_types_like_regular_users():
    rng = __import__("random").Random(1)
    assert qr.humanize("I don't think you get it", rng) == "i dont think u get it"


def test_humanize_leaves_text_alone_at_zero_rates(monkeypatch):
    monkeypatch.setattr(qr, "crowd_profile", lambda: {"shortforms": {"rates": {"u": 0.0}}})
    monkeypatch.setattr(qr, "profile", lambda: {"starts_lowercase_pct": 0.0})
    assert qr.humanize("You don't say", __import__("random").Random(1)) == "You don't say"


def test_humanize_does_not_touch_words_inside_words():
    rng = __import__("random").Random(1)
    assert qr.humanize("your youth, Iowa", rng) == "your youth, Iowa"  # 'ur' rate is 0 here; no partial matches


def test_each_viewers_copy_is_stable_and_casualized():
    post = _post(quick_replies=["I think you should see this"])
    viewer = uuid.uuid4()
    assert qr.draft_for(post, viewer) == qr.draft_for(post, viewer) == "i think u should see this"


def test_each_viewer_gets_a_stable_draft_and_viewers_spread_out():
    post = _post(quick_replies=GOOD)
    viewer = uuid.uuid4()
    assert qr.draft_for(post, viewer) == qr.draft_for(post, viewer)
    picks = {qr.draft_for(post, uuid.uuid4()) for _ in range(40)}
    assert len(picks) >= 4
    assert qr.draft_for(_post(quick_replies=[]), viewer) is None


# ---- prompt ----
def test_prompt_has_the_post_real_pairs_and_the_reaction_plan():
    rng = __import__("random").Random(1)
    sponsored = qr.build_messages(_post(), rng=rng)[1]["content"]
    assert "solana just hit new highs" in sponsored and "@solana" in sponsored
    assert "post: solana chart update" in sponsored and "reply: the chart looks cooked ngl" in sponsored
    assert "nuance" not in sponsored  # clients' posts never get pushback
    assert "@grok" not in sponsored  # ...or "@grok is this legit?"
    assert "absolute shit take" not in sponsored  # no profanity in examples for sponsored posts
    creator = qr.build_messages(_post(is_sponsored=False, platform="web"), rng=rng)[1]["content"]
    assert "nuance" in creator and "grok: only if the post has something checkable" in creator
    system = qr.build_messages(_post(), rng=rng)[0]["content"]
    assert "from a client we promote (a project, founder or creator): positive, neutral" in system
    assert "question: one short, curious question about what they announced" in sponsored
    assert "banter: light humor that's on their side" in sponsored
    creator_system = qr.build_messages(_post(is_sponsored=False, platform="web"), rng=rng)[0]["content"]
    assert "client we promote" not in creator_system
    assert "community member's post: positive, curious or mildly skeptical" in creator_system
    assert "banter: a light, friendly joke, never mocking them" in qr.build_messages(
        _post(is_sponsored=False, platform="web"), rng=rng)[1]["content"]
    assert "reply: @grok is this true?" in creator  # real, well-performing @grok questions as examples
    assert "reply: oh this is actually clean ngl" in creator  # regular users' replies as examples too
    assert "lol (5.0%)" in qr.build_messages(_post(), rng=rng)[0]["content"]  # measured words people drop in


# ---- models ----
async def test_sponsored_posts_use_the_better_models():
    router = FakeRouter({"better/one": (200, _answer(GOOD))})
    async with router.client() as client:
        drafts, model = await qr.write_drafts(_post(), client=client)
    assert model == "better/one" and router.models == ["better/one"] and len(drafts) == 6


async def test_creator_posts_fall_back_when_a_free_model_is_rate_limited():
    router = FakeRouter({"free/one:free": (429, {"error": "rate limited"}), "free/two:free": (200, _answer(GOOD))})
    async with router.client() as client:
        drafts, model = await qr.write_drafts(_post(is_sponsored=False, platform="web"), client=client)
    assert router.models == ["free/one:free", "free/two:free"] and model == "free/two:free" and drafts


async def test_a_model_that_writes_slop_is_skipped():
    slop = ["Great point!", "Love this!", "Absolutely incredible", "Thanks for sharing", "Game changer", "Kudos"]
    router = FakeRouter({"better/one": (200, _answer(slop)), "better/two": (200, _answer(GOOD))})
    async with router.client() as client:
        _, model = await qr.write_drafts(_post(), client=client)
    assert model == "better/two"


async def test_no_key_means_no_calls(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api", "")
    router = FakeRouter({})
    async with router.client() as client:
        assert await qr.write_drafts(_post(), client=client) == ([], "")
    assert router.models == []


# ---- storage ----
async def test_generate_stores_drafts_once(db_session, make_user):
    owner = await make_user()
    post = _post(user_id=owner.id)
    db_session.add(post)
    await db_session.commit()
    router = FakeRouter({"better/one": (200, _answer(GOOD))})
    async with router.client() as client:
        assert await qr.generate_for_post(db_session, post.id, client=client) == "done"
        assert await qr.generate_for_post(db_session, post.id, client=client) == "skipped"
    await db_session.refresh(post)
    assert len(post.quick_replies) == 6 and post.quick_replies_model == "better/one" and post.quick_replies_at
    assert router.models == ["better/one"]  # the second run didn't call a model


async def test_generate_reports_failure_so_the_job_retries(db_session, make_user):
    owner = await make_user()
    post = _post(user_id=owner.id)
    db_session.add(post)
    await db_session.commit()
    router = FakeRouter({"better/one": (503, {}), "better/two": (503, {})})
    async with router.client() as client:
        assert await qr.generate_for_post(db_session, post.id, client=client) == "failed"


async def test_feed_gives_each_viewer_their_draft(db_session, make_user):
    owner, viewer = await make_user(), await make_user()
    post = _post(user_id=owner.id, quick_replies=GOOD)
    db_session.add(post)
    await db_session.commit()
    out = await feed.format_post(db_session, post, viewer)
    assert out["quick_reply"] == qr.draft_for(post, viewer.id)


# ---- triggers ----
async def test_new_sponsored_posts_are_queued_for_drafts(db_session, monkeypatch):
    queued = []

    async def fake_queue(ids):
        queued.extend(ids)

    monkeypatch.setattr(qr, "queue", fake_queue)
    from app.core.time_utils import utcnow
    from app.models.sponsored_account import SponsoredAccount
    db_session.add(SponsoredAccount(x_username="sol", x_user_id="42", display_name="Sol", is_active=True,
                                    karma_per_post=Decimal("100"), active_since=utcnow()))
    await db_session.commit()
    tweet = {"id": "777", "text": "gm", "createdAt": utcnow().strftime("%a %b %d %H:%M:%S +0000 %Y"),
             "author": {"id": "42", "userName": "sol", "name": "Sol"}}
    created = await sponsors.ingest_tweets(db_session, [tweet])
    assert [p.id for p in created] == queued and len(queued) == 1


async def test_a_submitted_post_is_queued_for_drafts(db_session, make_user, monkeypatch):
    queued = []

    async def fake_queue(ids):
        queued.extend(ids)

    monkeypatch.setattr(qr, "queue", fake_queue)
    user = await make_user(x_username="poster", credits=Decimal("50"), total_credits_earned=Decimal("50"))
    db_session.add(XProfile(user_id=user.id, x_user_id="X9", username="poster", score=0))
    db_session.add_all([SiteSetting(key="POST_COST_MIN", value="10", data_type="int"),
                        SiteSetting(key="POST_COST_MAX", value="200", data_type="int")])
    await db_session.commit()
    site_settings._cache.clear()

    class _Twitter:
        async def get_tweet_content(self, tweet_id):
            return {"author_id": "X9", "author_username": "poster", "text": "my first post"}

    with patch("app.services.posts.get_twitter_client", return_value=_Twitter()):
        out = await posts_svc.submit_post(db_session, user=user, x_link="https://x.com/poster/status/55",
                                          karma_amount=10)
    assert queued == [uuid.UUID(out["post_id"])]


# ---- @grok ----
@pytest.mark.parametrize("raw,expected", [
    ("@solana @grok is this true?", "@grok is this true?"),
    ("@Grok explain this chart", "@grok explain this chart"),
    ("is this true @grok", "@grok is this true"),
])
def test_clean_keeps_a_grok_question(raw, expected):
    assert qr.clean(raw) == expected


def test_clean_drops_other_mentions_inside_a_reply():
    assert qr.clean("ask @vitalik about this") is None


def test_at_most_one_grok_draft_and_none_when_not_allowed():
    rng = __import__("random").Random(1)
    raw = ["@grok is this true?", "@grok source?", "cheap fees go brr"]
    assert qr.finalize(raw, rng=rng, allow_grok=True) == ["@grok is this true?", "cheap fees go brr"]
    assert all(not d.startswith("@grok") for d in qr.finalize(raw, rng=rng, allow_grok=False))


async def test_sponsored_drafts_never_ask_grok():
    router = FakeRouter({"better/one": (200, _answer(["@grok is this legit?", *GOOD]))})
    async with router.client() as client:
        drafts, _ = await qr.write_drafts(_post(), client=client)
    assert drafts and not any("@grok" in d for d in drafts)


async def test_creator_drafts_may_ask_grok_once():
    router = FakeRouter({"free/one:free": (200, _answer([*GOOD[:5], "@grok is this true?", "@grok source?"]))})
    async with router.client() as client:
        drafts, _ = await qr.write_drafts(_post(is_sponsored=False, platform="web"), client=client)
    assert sum(1 for d in drafts if d.startswith("@grok")) <= 1


# ---- tone: sponsored never negative, creators free ----
NEGATIVE = ["kinda lame tbh", "nobody asked for this", "feels like a consolation prize", "mid announcement"]


def test_sponsored_drafts_are_never_negative():
    rng = __import__("random").Random(1)
    assert qr.finalize([*NEGATIVE, "this is clean"], rng=rng, sponsored=True) == ["this is clean"]


def test_community_drafts_may_be_mildly_negative_but_never_harsh():
    rng = __import__("random").Random(1)
    mild = ["kinda lame tbh", "feels like a consolation prize", "idk feels like same cycle different year tbh"]
    harsh = ["nobody asked for this", "this is a scam lol", "cope harder", "ngmi"]
    assert qr.finalize([*mild, *harsh], rng=rng, sponsored=False) == mild


async def test_a_model_that_goes_negative_on_a_client_falls_through():
    router = FakeRouter({"better/one": (200, _answer([*NEGATIVE, "love the timing"])),
                         "better/two": (200, _answer(GOOD))})
    async with router.client() as client:
        drafts, model = await qr.write_drafts(_post(), client=client)
    assert model == "better/two" and not any(n in drafts for n in NEGATIVE)


def test_a_sponsored_post_never_shows_an_old_negative_draft():
    post = _post(quick_replies=["kinda lame tbh", "clean drop"])
    for _ in range(20):
        assert qr.draft_for(post, uuid.uuid4()) == "clean drop"


# ---- the casualness dial (QUICK_REPLY_CASUALNESS) ----
def test_casualness_zero_keeps_the_draft_as_written(monkeypatch):
    monkeypatch.setattr(qr, "crowd_profile", lambda: {"most_liked": {"starts_lowercase_pct": 50.0},
                                                      "shortforms": {"rates": {"u": 0.5}}})
    for seed in range(50):
        assert qr.humanize("You get it", __import__("random").Random(seed), casualness=0) == "You get it"


def test_casualness_scales_the_measured_rate_but_never_past_95_percent(monkeypatch):
    monkeypatch.setattr(qr, "crowd_profile", lambda: {"shortforms": {"rates": {"u": 0.5}}})
    monkeypatch.setattr(qr, "profile", lambda: {"starts_lowercase_pct": 0.0})
    hits = sum(qr.humanize("you get it", __import__("random").Random(s), casualness=3) == "u get it"
               for s in range(400))
    assert 340 <= hits < 400  # ~95%: capped, and still not every single time
