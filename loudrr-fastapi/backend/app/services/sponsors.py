"""Sponsored accounts — admin-added X handles whose posts become raid posts.

An admin adds a handle; we resolve it on X and put it on the gateway's
realtime monitor list. The worker (sponsor_stream.py) gets new posts two ways
— pushed over the gateway websocket, and a short-interval search poll that
catches anything the push misses — and hands every tweet to `ingest_tweets`,
which turns each NEW ORIGINAL post of an active sponsor (not a reply, not a
retweet) into a sponsored Post:

  * owned by the platform user (no Telegram account, never pays or earns)
  * escrow = the sponsor's karma_per_post, platform-funded (nothing is spent
    to create it, and nothing is refunded when it expires)
  * platform "sponsor", is_sponsored=True — the feed shows these first and
    settlement adds the sponsored-XP bonus for engagers

One post per tweet is guaranteed by a partial unique index, so the push, the
poll and a second worker can all deliver the same tweet safely.
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Numeric, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from app.core.time_utils import utcnow
from app.integrations.x_stream import GatewayUnavailable, XStreamClient
from app.models.audit_log import AuditLog
from app.models.engagement import Engagement
from app.models.post import Post
from app.models.sponsored_account import SponsoredAccount
from app.models.user import User
from app.services import kill_switches, quick_replies

logger = logging.getLogger(__name__)

SPONSOR_PLATFORM = "sponsor"
# the platform account that owns every sponsored post (fixed id, created on
# first use; no telegram_id, so nobody can log in as it)
PLATFORM_USER_ID = uuid.UUID("00000000-0000-4000-8000-00000000500d")
PLATFORM_REFERRAL_CODE = "SPONSORED"
PLATFORM_DISPLAY_NAME = "Loudrr Sponsored"

DEFAULT_KARMA_PER_POST = Decimal("100")
MAX_KARMA_PER_POST = Decimal("100000")
# a tweet older than this is never imported (e.g. the catch-up after a long
# outage) — it would expire from the feed almost immediately anyway
MAX_TWEET_AGE = timedelta(hours=12)
# X clocks vs ours: a post made seconds before the admin clicked Add counts
ACTIVE_SINCE_GRACE = timedelta(minutes=2)

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_HANDLE_IN_URL_RE = re.compile(r"(?:twitter\.com|x\.com)/@?([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.I)
_SCREEN_NAME_PARAM_RE = re.compile(r"[?&]screen_name=@?([A-Za-z0-9_]{1,15})(?:[&#]|$)", re.I)
# x.com/<these>/... are X's own pages, not profiles (x.com/i/web/status/..,
# x.com/home, x.com/intent/user?screen_name=..)
_RESERVED_PATHS = frozenset({
    "i", "home", "search", "intent", "explore", "notifications", "messages", "settings",
    "share", "hashtag", "compose", "login", "logout", "signup", "tos", "privacy",
})


def normalize_handle(raw: str) -> str:
    """'@Name', 'name', 'https://x.com/Name' -> 'name'. BadRequest otherwise."""
    value = (raw or "").strip()
    m = _HANDLE_IN_URL_RE.search(value)
    if m:
        value = m.group(1)
        if value.lower() in _RESERVED_PATHS:
            param = _SCREEN_NAME_PARAM_RE.search(raw)
            if not param:
                raise BadRequest("Paste the account's profile link or its @handle")
            value = param.group(1)
    value = value.lstrip("@").strip()
    if not _HANDLE_RE.match(value):
        raise BadRequest("Enter a valid X handle, e.g. @loudrr")
    return value.lower()


# ---- tweets ----
def tweets_in_frame(frame) -> list[dict]:
    """The tweets carried by one websocket frame (tolerant of the shapes the
    gateway may use: {"tweets": [...]}, {"tweet": {...}}, {"data": ...})."""
    if not isinstance(frame, dict) or frame.get("event_type") in ("connected", "ping"):
        return []
    for key in ("tweets", "tweet", "data"):
        value = frame.get(key)
        if isinstance(value, list):
            return [t for t in value if isinstance(t, dict)]
        if isinstance(value, dict):
            if isinstance(value.get("tweets"), list):
                return [t for t in value["tweets"] if isinstance(t, dict)]
            if value.get("id"):
                return [value]
    if frame.get("id") and isinstance(frame.get("author"), dict):
        return [frame]
    return []


def is_original_post(tweet: dict) -> bool:
    """The account's own post: not a reply (even to itself), not a retweet.
    Quote posts count — they are the account's own post."""
    if tweet.get("isReply") or tweet.get("inReplyToId") or tweet.get("inReplyToUserId"):
        return False
    if tweet.get("isRetweet") or tweet.get("retweeted_tweet"):
        return False
    return not (tweet.get("text") or "").startswith("RT @")


def parse_tweet_time(value) -> datetime | None:
    """X's 'Tue Jul 28 14:30:39 +0000 2026' (or ISO) -> naive UTC."""
    if not value or not isinstance(value, str):
        return None
    for parse in (
        lambda v: datetime.strptime(v, "%a %b %d %H:%M:%S %z %Y"),
        lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
    ):
        try:
            dt = parse(value)
        except ValueError:
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None


def _media_urls(tweet: dict) -> list[str]:
    urls = []
    for media in ((tweet.get("extendedEntities") or {}).get("media") or []):
        if isinstance(media, dict):
            url = media.get("media_url_https") or media.get("url")
            if url:
                urls.append(url)
    return urls


async def platform_user(db) -> User:
    """The platform account that owns sponsored posts (created on first use)."""
    user = await db.get(User, PLATFORM_USER_ID)
    if user is not None:
        return user
    try:
        async with db.begin_nested():
            db.add(User(
                id=PLATFORM_USER_ID, telegram_id=None, referral_code=PLATFORM_REFERRAL_CODE,
                display_name=PLATFORM_DISPLAY_NAME,
                credits=Decimal("0"), total_credits_earned=Decimal("0"),
                total_credits_spent=Decimal("0"),
            ))
    except IntegrityError:  # created concurrently
        pass
    return await db.get(User, PLATFORM_USER_ID)


async def _match_sponsor(db, *, author_id: str, username: str) -> SponsoredAccount | None:
    conditions = []
    if author_id:
        conditions.append(SponsoredAccount.x_user_id == author_id)
    if username:
        conditions.append(SponsoredAccount.x_username == username)
    if not conditions:
        return None
    rows = (await db.execute(select(SponsoredAccount).where(or_(*conditions)))).scalars().all()
    # the permanent id wins over a handle someone else may now own
    by_id = [r for r in rows if author_id and r.x_user_id == author_id]
    if by_id:
        return by_id[0]
    # a handle match is only trusted when ids can't contradict it: after a
    # sponsor renames, whoever registers its old @handle is NOT the sponsor
    by_handle = [r for r in rows if not (author_id and r.x_user_id)]
    for row in rows:
        if row not in by_handle:
            logger.warning(
                "sponsor @%s: tweet from a different X account (id %s, expected %s) — handle changed hands?",
                row.x_username, author_id, row.x_user_id,
            )
    return (by_handle or [None])[0]


async def ingest_tweet(db, tweet: dict, *, now: datetime | None = None) -> Post | None:
    """Create the sponsored post for this tweet, or None when it doesn't
    qualify (unknown/paused account, reply, retweet, too old, already in)."""
    if not isinstance(tweet, dict):
        return None
    # admin kill switch — the stream keeps polling, we just stop minting posts
    if not await kill_switches.is_enabled(db, kill_switches.SPONSOR_INGEST_ENABLED):
        return None
    tweet_id = str(tweet.get("id") or "").strip()
    if not tweet_id.isdigit():
        return None
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    author_id = str(author.get("id") or "").strip()
    handle = str(author.get("userName") or "").strip()
    sponsor = await _match_sponsor(db, author_id=author_id, username=handle.lower())
    if sponsor is None or not sponsor.is_active or not is_original_post(tweet):
        return None

    now = now or utcnow()
    created = parse_tweet_time(tweet.get("createdAt"))
    if created is not None:
        if created < sponsor.active_since - ACTIVE_SINCE_GRACE:
            return None  # posted before the account was added / resumed
        if now - created > MAX_TWEET_AGE:
            return None

    already = await db.scalar(
        select(Post.id).where(Post.platform == SPONSOR_PLATFORM, Post.tweet_id == tweet_id)
    )
    if already is not None:
        return None

    poster = await platform_user(db)
    handle = handle or sponsor.x_username
    post = Post(
        user_id=poster.id, sponsor_id=sponsor.id, is_sponsored=True, platform=SPONSOR_PLATFORM,
        x_link=f"https://x.com/{handle}/status/{tweet_id}", tweet_id=tweet_id,
        tweet_text=str(tweet.get("text") or ""),
        tweet_author_name=str(author.get("name") or sponsor.display_name or handle)[:100],
        tweet_author_username=handle[:50],
        tweet_author_avatar=str(author.get("profilePicture") or sponsor.avatar_url or "")[:500],
        tweet_media=_media_urls(tweet), tweet_created_at=created,
        escrow=sponsor.karma_per_post, initial_escrow=sponsor.karma_per_post,
    )
    try:
        async with db.begin_nested():
            db.add(post)
    except IntegrityError:
        return None  # another delivery of the same tweet won the race

    # one UPDATE: the push and the poll can import this sponsor's posts at the
    # same moment in separate sessions, and a Python-side += would lose a count
    values = {
        "posts_created": SponsoredAccount.posts_created + 1,
        "last_tweet_id": tweet_id, "last_post_at": now, "updated_at": now,
    }
    if author_id and not sponsor.x_user_id:
        values["x_user_id"] = author_id
    if author.get("name"):
        values["display_name"] = str(author["name"])[:100]
    if author.get("profilePicture"):
        values["avatar_url"] = str(author["profilePicture"])[:500]
    await db.execute(
        update(SponsoredAccount).where(SponsoredAccount.id == sponsor.id).values(**values)
        .execution_options(synchronize_session="evaluate")
    )
    return post


async def ingest_tweets(db, tweets) -> list[Post]:
    """Ingest a batch (oldest first is best) and commit once."""
    created = []
    for tweet in tweets:
        try:
            # per-tweet savepoint: one tweet the DB rejects (bad bytes, a
            # deadlock with the other loop) is skipped, not the whole batch
            async with db.begin_nested():
                post = await ingest_tweet(db, tweet)
        except Exception:
            logger.exception(
                "sponsor ingest: skipped tweet %s",
                tweet.get("id") if isinstance(tweet, dict) else tweet,
            )
            continue
        if post is not None:
            created.append(post)
    await db.commit()
    for post in created:
        logger.info(
            "sponsored post created: @%s tweet %s (%s karma)",
            post.tweet_author_username, post.tweet_id, post.initial_escrow,
        )
    await quick_replies.queue([post.id for post in created])
    return created


# ---- gateway upkeep ----
async def sync_monitors(db, client: XStreamClient) -> dict:
    """Make the gateway monitor list match the table: every active sponsor is
    monitored, paused ones are not. Handles we don't know are left alone."""
    sponsors = (await db.execute(select(SponsoredAccount))).scalars().all()
    await db.commit()  # don't hold a connection across the gateway calls
    monitored = {
        str(row.get("x_user_screen_name") or row.get("x_user_name") or "").lower()
        for row in await client.list_monitors()
    }
    added, removed, failed = [], [], []
    for s in sponsors:
        try:
            if s.is_active and s.x_username not in monitored:
                await client.add_monitor(s.x_username)
                added.append(s.x_username)
            elif not s.is_active and s.x_username in monitored:
                await client.remove_monitor(s.x_username)
                removed.append(s.x_username)
        except GatewayUnavailable as e:
            logger.warning("sponsor monitor sync @%s failed: %s", s.x_username, e)
            failed.append(s.x_username)
    if added or removed:
        logger.info("sponsor monitors synced: added=%s removed=%s", added, removed)
    return {"added": added, "removed": removed, "failed": failed}


async def _rename(db, sponsor: SponsoredAccount, new_handle: str, *, client: XStreamClient,
                  info: dict | None = None, admin_id=None) -> bool:
    """Point a sponsor at its new @handle (same X account, renamed). False if
    another sponsor already uses that handle."""
    old = sponsor.x_username
    taken = await db.scalar(
        select(SponsoredAccount.id).where(
            SponsoredAccount.x_username == new_handle, SponsoredAccount.id != sponsor.id,
        )
    )
    if taken is not None:
        logger.warning("sponsor @%s renamed to @%s, but that handle is another sponsor", old, new_handle)
        await db.commit()
        return False
    sponsor.x_username = new_handle
    if info:
        sponsor.display_name = str(info.get("name") or sponsor.display_name)[:100]
        sponsor.avatar_url = str(info.get("profilePicture") or sponsor.avatar_url)[:500]
    sponsor.updated_at = utcnow()
    _audit(db, admin_id=admin_id, action="rename_sponsor", sponsor=sponsor, detail={"old_x_username": old})
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    logger.info("sponsor @%s renamed to @%s", old, new_handle)
    if sponsor.is_active and client.configured:
        try:
            await client.add_monitor(new_handle)
            await client.remove_monitor(old)
        except GatewayUnavailable as e:
            # the periodic sync re-adds the new handle; the old entry is no
            # longer ours to find, so say which one to remove by hand
            logger.warning("sponsor rename @%s -> @%s: gateway monitor not moved (remove @%s): %s",
                           old, new_handle, old, e)
    return True


async def refresh_handles(db, client: XStreamClient) -> list[tuple[str, str]]:
    """Catch renames: look active sponsors up by their permanent X id and move
    any whose @handle changed (the poll searches by handle)."""
    rows = (
        await db.execute(
            select(SponsoredAccount).where(
                SponsoredAccount.is_active.is_(True), SponsoredAccount.x_user_id != "",
            )
        )
    ).scalars().all()
    await db.commit()
    if not rows:
        return []
    users = {str(u.get("id")): u for u in await client.users_by_ids([r.x_user_id for r in rows])}
    renamed = []
    for sponsor in rows:
        info = users.get(sponsor.x_user_id)
        new_handle = str((info or {}).get("userName") or "").lower()
        if not new_handle or new_handle == sponsor.x_username or not _HANDLE_RE.match(new_handle):
            continue
        old = sponsor.x_username
        if await _rename(db, sponsor, new_handle, client=client, info=info):
            renamed.append((old, new_handle))
    return renamed


# X caps a search query at ~512 chars; each chunk ORs as many handles as fit
_QUERY_MAX_CHARS = 450
# a page this full may have more behind it; a short page is the whole window
_FULL_PAGE = 15
MAX_POLL_PAGES = 10


def search_queries(handles, since: datetime) -> list[str]:
    """`(from:a OR from:b) -filter:replies -filter:retweets since_time:<unix>`,
    split into as few queries as fit X's length limit."""
    since_unix = int(since.replace(tzinfo=timezone.utc).timestamp())
    suffix = f" -filter:replies -filter:retweets since_time:{since_unix}"
    queries, chunk = [], []

    def render(names):
        return "(" + " OR ".join(f"from:{h}" for h in names) + ")" + suffix

    for handle in handles:
        if chunk and len(render([*chunk, handle])) > _QUERY_MAX_CHARS:
            queries.append(render(chunk))
            chunk = []
        chunk.append(handle)
    if chunk:
        queries.append(render(chunk))
    return queries


async def poll_since(db) -> datetime:
    """Where the worker's first poll starts: the oldest point any active sponsor
    could have an unimported post — per sponsor the later of its last import
    and when it was added/resumed (nothing before that is imported anyway) —
    never further back than MAX_TWEET_AGE."""
    oldest = await db.scalar(
        # GREATEST skips NULLs: a sponsor with no import yet uses active_since
        select(func.min(func.greatest(SponsoredAccount.last_post_at, SponsoredAccount.active_since)))
        .where(SponsoredAccount.is_active.is_(True))
    )
    await db.commit()
    floor = utcnow() - MAX_TWEET_AGE
    return max(oldest, floor) if oldest is not None else utcnow()


async def poll_new_posts(db, client: XStreamClient, *, since: datetime) -> int:
    """Search every active sponsor's original posts since `since` and ingest
    them. Raises GatewayUnavailable so the caller keeps its window."""
    handles = (
        await db.execute(
            select(SponsoredAccount.x_username)
            .where(SponsoredAccount.is_active.is_(True))
            .order_by(SponsoredAccount.x_username)
        )
    ).scalars().all()
    await db.commit()  # don't hold a connection across the gateway calls
    created = 0
    for query in search_queries(handles, since):
        tweets, cursor = [], ""
        for page in range(MAX_POLL_PAGES):
            batch, cursor, has_next = await client.search_latest(query, cursor)
            tweets.extend(batch)
            if len(batch) < _FULL_PAGE or not has_next or not cursor:
                break
            if page == MAX_POLL_PAGES - 1:
                logger.warning("sponsor poll: page cap hit for %r; older posts skipped", query[:80])
        # oldest first, so last_tweet_id/last_post_at end on the newest
        tweets.sort(key=lambda t: int(t["id"]) if str(t.get("id") or "").isdigit() else 0)
        created += len(await ingest_tweets(db, tweets))
    return created


# ---- admin ----
def _check_karma(value) -> Decimal:
    try:
        karma = Decimal(str(value))
    except Exception:
        raise BadRequest("Karma per post must be a number")
    if not karma.is_finite() or karma < 1 or karma > MAX_KARMA_PER_POST:
        raise BadRequest(f"Karma per post must be between 1 and {MAX_KARMA_PER_POST}")
    return karma


def _audit(db, *, admin_id, action, sponsor: SponsoredAccount, detail=None):
    db.add(AuditLog(
        actor_id=admin_id, action=action, target_type="sponsored_account",
        target_id=sponsor.id, detail={"x_username": sponsor.x_username, **(detail or {})},
    ))


async def list_sponsors(db, *, sponsor_id=None) -> list[dict]:
    query = select(SponsoredAccount).order_by(SponsoredAccount.created_at.desc())
    if sponsor_id is not None:
        query = query.where(SponsoredAccount.id == sponsor_id)
    sponsors = (await db.execute(query)).scalars().all()
    ids = [s.id for s in sponsors]
    post_stats, engagement_stats = {}, {}
    if ids:
        for sid, total, active in (
            await db.execute(
                select(
                    Post.sponsor_id, func.count(),
                    func.count(case((Post.status == "active", 1))),
                ).where(Post.sponsor_id.in_(ids)).group_by(Post.sponsor_id)
            )
        ).all():
            post_stats[sid] = (total, active)
        # karma paid = the amount settlement recorded on each credited
        # engagement. Not initial_escrow - escrow: an expired sponsored post's
        # leftover escrow is zeroed without a refund, which would read as paid.
        paid = Engagement.verification_data["amount"].astext.cast(Numeric(12, 4))
        for sid, count, paid_total in (
            await db.execute(
                select(
                    Post.sponsor_id, func.count(Engagement.id),
                    func.coalesce(func.sum(case((Engagement.credit_granted.is_(True), paid))), 0),
                )
                .join(Engagement, Engagement.post_id == Post.id)
                .where(Post.sponsor_id.in_(ids)).group_by(Post.sponsor_id)
            )
        ).all():
            engagement_stats[sid] = (count, paid_total)
    rows = []
    for s in sponsors:
        total, active = post_stats.get(s.id, (0, 0))
        engagements, paid_total = engagement_stats.get(s.id, (0, 0))
        rows.append(serialize(s, stats=(total, active, paid_total), engagements=engagements))
    return rows


async def sponsor_row(db, sponsor_id) -> dict:
    """One sponsor with the same stats the list shows."""
    rows = await list_sponsors(db, sponsor_id=sponsor_id)
    if not rows:
        raise NotFound("Sponsored account not found")
    return rows[0]


def serialize(s: SponsoredAccount, *, stats=None, engagements: int = 0) -> dict:
    total, active, paid = stats or (0, 0, 0)
    return {
        "id": str(s.id),
        "x_username": s.x_username,
        "x_user_id": s.x_user_id,
        "display_name": s.display_name,
        "avatar_url": s.avatar_url,
        "is_active": s.is_active,
        "karma_per_post": float(s.karma_per_post),
        "notes": s.notes,
        "posts_created": s.posts_created,
        "active_posts": int(active),
        "posts_total": int(total),
        "karma_paid": float(paid),
        "engagements": int(engagements),
        "last_post_at": s.last_post_at.isoformat() if s.last_post_at else None,
        "last_tweet_id": s.last_tweet_id or None,
        "active_since": s.active_since.isoformat() if s.active_since else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


async def add_sponsor(db, *, admin_id, handle: str, karma_per_post=DEFAULT_KARMA_PER_POST,
                      notes: str = "", client: XStreamClient) -> SponsoredAccount:
    username = normalize_handle(handle)
    karma = _check_karma(karma_per_post)
    existing = await db.scalar(
        select(SponsoredAccount).where(SponsoredAccount.x_username == username)
    )
    if existing is not None:
        hint = "" if existing.is_active else " (paused — resume it instead)"
        raise Conflict(f"@{username} is already a sponsored account{hint}")
    if not client.configured:
        raise ServiceUnavailable("The Loudrr gateway isn't configured (LOUDRR_GATEWAY_API)")
    await db.commit()  # release the connection during the gateway calls

    try:
        info = await client.user_info(username)
    except GatewayUnavailable as e:
        logger.warning("add sponsor @%s: gateway unavailable: %s", username, e)
        raise ServiceUnavailable("Couldn't reach the Loudrr gateway — try again in a minute")
    if info is None:
        raise BadRequest(f"X account @{username} doesn't exist")
    if info.get("protected"):
        raise BadRequest(f"@{username} is a protected account — its posts can't be raided")
    x_user_id = str(info.get("id") or "")

    # the same X account, renamed since it was added: move that sponsor to the
    # new handle (keeps its settings and stats) instead of adding a second one
    renamed = x_user_id and await db.scalar(
        select(SponsoredAccount).where(SponsoredAccount.x_user_id == x_user_id)
    )
    if renamed:
        if not await _rename(db, renamed, username, client=client, info=info, admin_id=admin_id):
            raise Conflict(f"@{username} is already a sponsored account")
        return renamed
    await db.commit()

    # the poll finds posts without it; the monitor only feeds the push, and the
    # stream's periodic sync retries it
    try:
        await client.add_monitor(username)
    except GatewayUnavailable as e:
        logger.warning("add sponsor @%s: monitor not added yet: %s", username, e)

    sponsor = SponsoredAccount(
        x_username=username, x_user_id=x_user_id,
        display_name=str(info.get("name") or "")[:100],
        avatar_url=str(info.get("profilePicture") or "")[:500],
        karma_per_post=karma, notes=(notes or "").strip(), created_by_id=admin_id,
        active_since=utcnow(),
    )
    db.add(sponsor)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict(f"@{username} is already a sponsored account")
    _audit(db, admin_id=admin_id, action="add_sponsor", sponsor=sponsor,
           detail={"karma_per_post": str(karma)})
    await db.commit()
    return sponsor


async def _get(db, sponsor_id) -> SponsoredAccount:
    sponsor = await db.get(SponsoredAccount, sponsor_id)
    if sponsor is None:
        raise NotFound("Sponsored account not found")
    return sponsor


async def update_sponsor(db, *, admin_id, sponsor_id, is_active: bool | None = None,
                         karma_per_post=None, notes: str | None = None,
                         client: XStreamClient) -> SponsoredAccount:
    sponsor = await _get(db, sponsor_id)
    handle, was_active = sponsor.x_username, sponsor.is_active
    if karma_per_post is not None:
        _check_karma(karma_per_post)  # reject before touching the gateway
    if is_active is not None and is_active != was_active:
        await db.commit()  # don't hold a connection across the gateway call
        try:
            if is_active:
                await client.add_monitor(handle)
            else:
                await client.remove_monitor(handle)
        except GatewayUnavailable as e:
            # never blocks: ingestion checks is_active, the poll doesn't need
            # the monitor, and the stream's periodic sync fixes the list
            logger.warning("sponsor @%s %s: monitor not updated yet: %s",
                           handle, "resume" if is_active else "pause", e)

    changes: dict = {}
    if karma_per_post is not None:
        karma = _check_karma(karma_per_post)
        if karma != sponsor.karma_per_post:
            changes["karma_per_post"] = str(karma)
            sponsor.karma_per_post = karma  # applies to future posts only
    if notes is not None and notes.strip() != sponsor.notes:
        sponsor.notes = notes.strip()
        changes["notes"] = True

    if is_active is not None and is_active != sponsor.is_active:
        if is_active:
            sponsor.active_since = utcnow()  # don't import what was posted while paused
        sponsor.is_active = is_active
        changes["is_active"] = is_active

    if changes:
        sponsor.updated_at = utcnow()
        _audit(db, admin_id=admin_id, action="update_sponsor", sponsor=sponsor, detail=changes)
    await db.commit()
    return sponsor


async def delete_sponsor(db, *, admin_id, sponsor_id, client: XStreamClient) -> None:
    """Remove the account. Its existing posts stay live until they expire."""
    sponsor = await _get(db, sponsor_id)
    await db.commit()  # don't hold a connection across the gateway call
    if client.configured:
        try:
            await client.remove_monitor(sponsor.x_username)
        except GatewayUnavailable as e:
            # once the row is gone nothing would ever remove the monitor
            logger.warning("delete sponsor @%s: gateway unavailable: %s", sponsor.x_username, e)
            raise ServiceUnavailable(
                "Couldn't remove the account from the gateway — pause it, or try again"
            )
    _audit(db, admin_id=admin_id, action="delete_sponsor", sponsor=sponsor)
    await db.delete(sponsor)
    await db.commit()
