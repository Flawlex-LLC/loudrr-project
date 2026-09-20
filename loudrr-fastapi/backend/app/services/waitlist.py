import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from sqlalchemy import func, nulls_last, or_, select, update

from app.core.errors import BadRequest, Conflict, NotFound
from app.core.time_utils import utcnow
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.repositories.user import UserRepository
from app.repositories.waitlist import WaitlistRepository
from app.services.waitlist_x_oauth import discard_proof, verify_confirmed_proof

logger = logging.getLogger(__name__)


@dataclass
class RegisterResult:
    entry: WaitlistEntry
    was_new: bool


@dataclass
class StatusResult:
    status: str  # approved | waitlisted | rejected | not_registered
    entry: WaitlistEntry | None = None


async def _unique_referral_code(
    users: UserRepository, waitlist: WaitlistRepository
) -> str:
    """8-char uppercase code, unique across both tables."""
    for _ in range(10):
        code = secrets.token_urlsafe(6)[:8].upper()
        if (
            not await users.exists(referral_code=code)
            and not await waitlist.exists(referral_code=code)
        ):
            return code
    raise RuntimeError("Could not generate unique referral code")


def _normalize_code(code: str | None) -> str:
    """Referral codes are generated uppercase; links and typing may not be."""
    code = (code or "").strip().upper()
    return code if 0 < len(code) <= 16 else ""


async def _resolve_referrer(
    users: UserRepository, waitlist: WaitlistRepository, code: str | None
):
    """Return (referrer_id, code_used). Silently ignores a bad code."""
    code = _normalize_code(code)
    if not code:
        return None, ""
    user = await users.get(referral_code=code)
    if user:
        return user.id, code
    # a code that belongs to another *entry* (not a user yet): no id to
    # link, but we still record which code they used
    if await waitlist.exists(referral_code=code):
        return None, code
    return None, ""


async def _load_submitted(db, entry_id: uuid.UUID, action: str) -> WaitlistEntry:
    """Load an entry that must still be 'submitted', or refuse the action.
    Shared by approve_entry and reject_entry — the state-machine guard.

    Row-locked (FOR UPDATE, re-read even if already in the session): an
    approve and a reject clicked at the same moment must not both pass the
    guard — the second one waits, sees the new status and gets a 409."""
    entry = (
        await db.execute(
            select(WaitlistEntry)
            .where(WaitlistEntry.id == entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise NotFound("waitlist entry not found")
    if entry.status != "submitted":
        raise Conflict(f"Entry is {entry.status!r}, cannot {action}")
    return entry


# ---- THE USE CASES ----
async def register_entry(db, *, tg_user: dict, payload) -> RegisterResult:
    """Register a waitlist entry. Idempotent on telegram_id."""
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise BadRequest("Missing Telegram ID")

    users = UserRepository(db)
    waitlist = WaitlistRepository(db)

    # idempotency on telegram_id — same user submitting twice is success.
    # Checked BEFORE proof verification so an already-registered user gets
    # 'already_registered' even when their echoed proof has since expired.
    existing = await waitlist.get(telegram_id=telegram_id)
    if existing:
        await discard_proof(db, telegram_id=telegram_id)
        await db.commit()
        return RegisterResult(entry=existing, was_new=False)
    # an account that already exists without an entry (e.g. a seeded admin)
    # would create an entry that can never be approved (the User is taken)
    if await users.exists(telegram_id=telegram_id):
        raise BadRequest("This Telegram account already has access — open the app")

    # X OAuth is now mandatory step 1 of the waitlist flow. The proof is a
    # signed itsdangerous token minted by the callback after the applicant
    # completes X OAuth — _verify_and_extract re-checks signature, iat freshness,
    # and cross-references the embedded tg_id against Telegram initData. On top
    # of that, the X account's owner must have confirmed the link in the
    # browser that authorized (a forwarded authorize URL never gets that far).
    try:
        x_username, x_user_id = await verify_confirmed_proof(
            db, payload.x_proof, telegram_id=telegram_id,
        )
    except BadRequest:
        # A concurrent double submit may have registered since the check
        # above, and discarded the handoff row in the process. Give the same
        # answer that check would give.
        race = await waitlist.get(telegram_id=telegram_id)
        if race is not None:
            return RegisterResult(entry=race, was_new=False)
        raise

    xu = x_username.lower()
    # case-insensitive checks need a raw filter, not filter_by
    if await waitlist.exists_where(func.lower(WaitlistEntry.x_username) == xu):
        raise BadRequest("X username already registered")
    if await users.exists_where(func.lower(User.x_username) == xu):
        raise BadRequest("X username already in use")
    # the immutable numeric X id survives handle renames — one X account backs
    # at most one entry. The `if x_user_id` guard skips legacy rows with "".
    if x_user_id and await waitlist.exists_where(
        WaitlistEntry.x_user_id == x_user_id
    ):
        raise BadRequest("X account already registered")

    referrer_id, code_used = await _resolve_referrer(
        users, waitlist, payload.referral_code
    )

    try:
        entry = await waitlist.create(
            telegram_id=telegram_id,
            telegram_username=tg_user.get("username", "") or "",
            telegram_display_name=tg_user.get("first_name", "") or "",
            x_username=x_username,
            x_link=f"https://x.com/{x_username}",
            x_verified=True,
            x_user_id=x_user_id,
            # region/niche are Enums in the schema, plain strings in the DB —
            # store .value ("europe"), or "" when not given
            region=payload.region.value if payload.region else "",
            niche=payload.niche.value if payload.niche else "",
            # cap at 5; model_dump() turns each Pydantic model into a dict
            other_platforms=[
                p.model_dump() for p in (payload.other_platforms or [])[:5]
            ],
            referral_code=await _unique_referral_code(users, waitlist),
            referrer_id=referrer_id,
            referral_code_used=code_used,
        )
    except Conflict:
        # the telegram_id race: another request inserted the same id between
        # our pre-check and this INSERT. Re-query and return that row as a
        # success (the x_username 400 already fired above, so a Conflict
        # here can only be the telegram_id clash).
        race = await waitlist.get(telegram_id=telegram_id)
        if race is not None:
            return RegisterResult(entry=race, was_new=False)
        raise
    # queue the "waitlist_submitted" side-effect in THIS transaction; the
    # outbox worker (Ch16) delivers the Telegram card
    from app.services.outbox import OutboxService
    await OutboxService.queue_waitlist_submitted(
        db, entry_id=entry.id, telegram_id=entry.telegram_id,
        x_username=entry.x_username,
    )
    # the proof has done its job — drop the handoff row in the same commit
    await discard_proof(db, telegram_id=telegram_id)
    await db.commit()
    logger.info("waitlist entry created: %s", entry.id)
    # Sign-up is one of the two moments a score is fetched (the other is the
    # applicant's Refresh button) — in the background, so registering stays
    # fast; the card shows "pending" until it lands. No queue (tests/dev) ->
    # nothing runs and the applicant can tap Refresh.
    from app.tasks.enqueue import enqueue
    await enqueue("fetch_waitlist_score", str(entry.id), job_id=f"score:{entry.id}")
    return RegisterResult(entry=entry, was_new=True)


async def get_status(db, *, telegram_id: int) -> StatusResult:
    """approved (a User row exists), rejected, waitlisted, or not_registered."""
    if await UserRepository(db).exists(telegram_id=telegram_id):
        return StatusResult(status="approved")
    entry = await WaitlistRepository(db).get(telegram_id=telegram_id)
    if entry is not None:
        status = "rejected" if entry.status == "rejected" else "waitlisted"
        return StatusResult(status=status, entry=entry)
    return StatusResult(status="not_registered")


_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


async def public_card(db, *, username: str) -> dict | None:
    """The public share card for @username, or None when that handle isn't on
    the waitlist (or was rejected). Stored data only — never fetches."""
    handle = (username or "").strip().lstrip("@")
    if not _HANDLE_RE.match(handle):
        return None
    entry = (
        await db.execute(
            select(WaitlistEntry)
            .where(
                func.lower(WaitlistEntry.x_username) == handle.lower(),
                WaitlistEntry.status != "rejected",
            )
            .order_by(WaitlistEntry.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if entry is None:
        return None
    user = await db.get(User, entry.created_user_id) if entry.created_user_id else None
    from app.services import scores
    card = await scores.stored_card(db, user=user, entry=None if user else entry)
    return {
        "x_username": entry.x_username,
        "score": card["score"],
        "tier": card["tier"],
        "followers": card["followers"],
        "followers_count": card["followers_count"],
        "referral_code": entry.referral_code,
    }


async def approve_entry(
    db, *, entry_id: uuid.UUID, admin_id: uuid.UUID
) -> User:
    """Approve a submitted entry: create the User, credit the referrer and
    audit-log it — all in one transaction."""
    users = UserRepository(db)

    # load it (row-locked) + enforce the "must be submitted" guard in one call
    entry = await _load_submitted(db, entry_id, "approve")
    entry.status = "approved"
    entry.approved_at = utcnow()
    entry.approved_by_id = admin_id

    # Carry the OAuth result over. The waitlist already proved ownership of
    # the handle (x_verified + the immutable x_user_id), so the new User must
    # not start as "unverified" — the mini-app would then either make them
    # OAuth a second time or, worse, skip onboarding entirely because
    # is_whitelisted was never set. Approval IS the whitelist.
    user = await users.create(
        telegram_id=entry.telegram_id,
        telegram_username=entry.telegram_username,
        x_username=entry.x_username,
        display_name=entry.telegram_display_name,
        referral_code=entry.referral_code,  # keep their existing code
        is_whitelisted=True,
        x_verified=entry.x_verified,
        x_verified_at=utcnow() if entry.x_verified else None,
    )
    entry.created_user_id = user.id
    # Seed the profile with the OAuth-proven numeric id (submit_post's
    # ownership check needs it) plus the score fetched at sign-up — approval
    # never fetches. With no sign-up score, first-run onboarding fetches it.
    from app.services import scores
    await scores.seed_user_from_entry(db, entry=entry, user=user)

    # Referral credit, keyed by the CODE used: referrals made while the
    # referrer was still waitlisted have no referrer_id (no User existed yet),
    # and the count lives on the referrer's waitlist entry either way (approved
    # users keep their entry's code). One atomic UPDATE — two approvals at once
    # can't lose an increment.
    code = _normalize_code(entry.referral_code_used)
    if code and code != entry.referral_code:
        await db.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.referral_code == code)
            .values(total_referrals=WaitlistEntry.total_referrals + 1)
        )
    db.add(AuditLog(
        actor_id=admin_id, action="approve_waitlist", target_type="waitlist_entry",
        target_id=entry.id, detail={"x_username": entry.x_username, "user_id": str(user.id)},
    ))
    # queue the "waitlist_approved" Telegram card in THIS transaction
    from app.services.outbox import OutboxService
    await OutboxService.queue_waitlist_approved(
        db, entry_id=entry.id, telegram_id=entry.telegram_id, x_username=entry.x_username,
    )
    await db.commit()
    return user


async def reject_entry(
    db,
    *,
    entry_id: uuid.UUID,
    admin_id: uuid.UUID,
    reason: str = "",
    internal_note: str = "",
) -> WaitlistEntry:
    """Reject a submitted entry.

    TWO free-text fields, and the difference is the whole point:

      * ``reason`` is PUBLIC. It is stored on the entry and rendered into the
        applicant's Telegram DM ("…was not approved at this time. Reason: X").
        Write it for the applicant. Empty is fine — the message then simply
        ends after "at this time." instead of trailing off mid-sentence.
      * ``internal_note`` is for the team. It reaches `audit_logs.detail` and
        stops there — never the outbox, never a DM.

    Before the split there was one field, the admin UI labelled it "internal —
    visible only in audit logs", and it was DM'd verbatim.
    """
    # the same shared guard — load it (row-locked), or refuse if not "submitted"
    entry = await _load_submitted(db, entry_id, "reject")
    entry.status = "rejected"
    entry.rejection_reason = reason
    entry.approved_by_id = admin_id
    entry.approved_at = utcnow()
    db.add(AuditLog(
        actor_id=admin_id, action="reject_waitlist", target_type="waitlist_entry",
        target_id=entry.id,
        detail={
            "x_username": entry.x_username,
            "reason": reason,
            "internal_note": internal_note,
        },
    ))
    # queue the "waitlist_rejected" Telegram card in THIS transaction —
    # PUBLIC reason only
    from app.services.outbox import OutboxService
    await OutboxService.queue_waitlist_rejected(
        db, entry_id=entry.id, telegram_id=entry.telegram_id,
        x_username=entry.x_username, reason=reason,
    )
    await db.commit()
    return entry


async def reopen_entry(
    db, *, entry_id: uuid.UUID, admin_id: uuid.UUID, internal_note: str = ""
) -> WaitlistEntry:
    """Undo a rejection: put the entry back in the review queue.

    A mis-rejection used to be unrecoverable from the panel, and it is a trap
    door rather than a dead end: `register_entry` is idempotent on
    telegram_id, so a rejected applicant who re-applies is told they're
    already on the waitlist — forever — while the panel, which only ever
    listed `status == 'submitted'`, couldn't see them at all.

    Rules:
      * only from `rejected` (re-opening an approval would need the User torn
        down too — that's a different, much bigger operation);
      * refused outright once a User exists for the entry, even on a rejected
        row (a hand-edited or legacy state), because approve_entry would then
        try to create a second User on the same telegram_id;
      * clears the rejection bookkeeping so the row is indistinguishable from
        a fresh submission;
      * carries the OAuth result forward via `x_verified_previously`, the
        column Ch10 added for exactly this and nothing ever wrote — on
        re-approval the applicant skips the X verification step they already
        passed.

    No Telegram message: the applicant was told they weren't approved, and
    "actually, you're pending again" is noise until a decision is made. The
    audit log records who re-opened it and why.
    """
    entry = (
        await db.execute(
            select(WaitlistEntry)
            .where(WaitlistEntry.id == entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise NotFound("waitlist entry not found")
    if entry.status != "rejected":
        raise Conflict(f"Entry is {entry.status!r}, only a rejected entry can be reopened")
    if entry.created_user_id is not None:
        raise Conflict("This entry already created a user account; it cannot be reopened")

    was_reason = entry.rejection_reason
    entry.x_verified_previously = bool(entry.x_verified_previously or entry.x_verified)
    entry.status = "submitted"
    entry.rejection_reason = ""
    entry.approved_at = None
    entry.approved_by_id = None
    db.add(AuditLog(
        actor_id=admin_id, action="reopen_waitlist", target_type="waitlist_entry",
        target_id=entry.id,
        detail={
            "x_username": entry.x_username,
            "cleared_reason": was_reason,
            "internal_note": internal_note,
            "x_verified_previously": entry.x_verified_previously,
        },
    ))
    await db.commit()
    return entry


async def admin_refresh_score(
    db, *, entry_id: uuid.UUID, admin_id: uuid.UUID
) -> dict:
    """Re-fetch one applicant's score on an admin's behalf.

    Scores are only ever fetched at sign-up and from the applicant's own
    Refresh button, so an entry whose sign-up fetch failed (7 of the seeded
    35) sat unscored forever and the reviewer had no way to ask for it.

    `scores.fetch_waitlist_score` no-ops when `score_updated_at` is already
    set (it guards the sign-up job against racing the user's Refresh), so
    clearing that stamp first is what makes this a real re-fetch rather than a
    "skipped". A failed provider call leaves it null, which is the same state
    a never-fetched entry is in.

    Prefers the arq queue so the request returns immediately; with no queue
    configured (tests, local dev) it runs the same job inline and reports the
    outcome.
    """
    entry = await db.get(WaitlistEntry, entry_id)
    if entry is None:
        raise NotFound("waitlist entry not found")
    if not entry.x_username:
        raise BadRequest("This entry has no X handle to look up")

    entry.score_updated_at = None
    db.add(AuditLog(
        actor_id=admin_id, action="refresh_waitlist_score",
        target_type="waitlist_entry", target_id=entry.id,
        detail={"x_username": entry.x_username},
    ))
    await db.commit()

    from app.tasks.enqueue import enqueue
    queued = await enqueue(
        "fetch_waitlist_score", str(entry.id), job_id=f"score:{entry.id}"
    )
    result = None
    if not queued:
        from app.services import scores
        result = await scores.fetch_waitlist_score(db, entry.id)
        await db.refresh(entry)
    return {"queued": queued, "result": result, "entry": _entry_row(entry)}


# ---- THE REVIEW QUEUE (admin panel) ----
# The panel used to call one endpoint that hardcoded
# `status == 'submitted' ORDER BY created_at ASC LIMIT 200` and returned a
# bare list. Three things followed from that: at 201 pending applicants the
# tail silently vanished with nothing on screen to say so; reviewing 35
# people meant scrolling a single unsorted, unsearchable column; and the
# approved / rejected entries — including every rejection reason ever
# written — were invisible, which is also why a mis-rejection had no undo.

#: The three states the panel can page through. `submitted` is the queue,
#: the other two are history.
REVIEW_STATUSES = ("submitted", "approved", "rejected")

#: Sortable columns. `tier` is a pure function of `score`
#: (services.tier.tier_for), so sorting by tier IS sorting by score — there
#: is no stored tier column to order on, and deriving one in SQL would just
#: duplicate the band table.
_SORT_COLUMNS = {
    "created": WaitlistEntry.created_at,
    "score": WaitlistEntry.score,
    "tier": WaitlistEntry.score,
}

MAX_PAGE_SIZE = 200


def _score_facts(score_data) -> dict:
    """The decision-relevant slice of the stored provider payload.

    The full blob is ~17 keys including a banner URL and the top-followers
    list; what a reviewer actually weighs is reach, age and what the account
    says it is. Everything is optional — plenty of entries have no
    score_data at all.
    """
    data = score_data if isinstance(score_data, dict) else {}
    return {
        "followers_count": data.get("followers_count"),
        "following_count": data.get("friends_count"),
        "tweets_count": data.get("tweets_count"),
        "smart_followers": data.get("smart_followers"),
        # ISO date string ("2012-07-25") — account age is the single best bot
        # tell, and it was already sitting in the JSON unused
        "register_date": data.get("register_date"),
        "bio": data.get("description") or "",
        "profile_name": data.get("name") or "",
        "avatar": data.get("avatar") or "",
        "x_blue_verified": bool(data.get("verified")),
        "category": data.get("category") or "",
    }


def _entry_row(
    entry: WaitlistEntry,
    *,
    referrer_handle: str = "",
    referral_code_uses: int = 0,
    reviewer_handle: str = "",
) -> dict:
    """One row of the review queue, JSON-ready."""
    from app.services import tier as tier_svc

    return {
        "id": str(entry.id),
        "status": entry.status,
        # ---- identity ----
        "telegram_id": entry.telegram_id,
        "telegram_username": entry.telegram_username or "",
        # 4 of the seeded pending entries have no Telegram @username at all;
        # the panel rendered them as a bare "—" with nothing to read, click
        # or search. The display name and numeric id are the fallbacks.
        "telegram_display_name": entry.telegram_display_name or "",
        "x_username": entry.x_username or "",
        "x_user_id": entry.x_user_id or "",
        "x_link": entry.x_link or "",
        "x_verified": entry.x_verified,
        "x_verified_previously": entry.x_verified_previously,
        # ---- signal ----
        "score": entry.score,
        "tier": tier_svc.tier_for(entry.score) if entry.score is not None else None,
        # null score + null score_updated_at = never fetched ("pending");
        # null score + a timestamp = the provider has nothing ("no score")
        "score_updated_at": (
            entry.score_updated_at.isoformat() if entry.score_updated_at else None
        ),
        **_score_facts(entry.score_data),
        # ---- profile ----
        "region": entry.region or "",
        "niche": entry.niche or "",
        "other_platforms": entry.other_platforms or [],
        # ---- referral ----
        "referral_code": entry.referral_code or "",
        "referral_code_used": entry.referral_code_used or "",
        "referrer_handle": referrer_handle,
        # how many applications in total came in on the code THIS entry used.
        # Five of the seeded pending entries share one code; that is either a
        # good evangelist or a referral ring, and either way the reviewer
        # should see it before approving the fifth one.
        "referral_code_uses": referral_code_uses,
        "total_referrals": entry.total_referrals,
        # ---- decision bookkeeping ----
        "rejection_reason": entry.rejection_reason or "",
        "decided_at": entry.approved_at.isoformat() if entry.approved_at else None,
        "decided_by_handle": reviewer_handle,
        "created_user_id": str(entry.created_user_id) if entry.created_user_id else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _handle_of(user: User) -> str:
    return user.telegram_username or user.x_username or (
        f"#{user.telegram_id}" if user.telegram_id else ""
    )


async def _referral_context(db, rows) -> tuple[dict, dict]:
    """(code -> referrer handle, code -> total applications on that code).

    Two batched queries for the whole page, not two per row. A code can
    belong to a User (an approved referrer) or to another still-waitlisted
    WaitlistEntry — resolve both.
    """
    codes = {e.referral_code_used for e in rows if e.referral_code_used}
    if not codes:
        return {}, {}

    handles: dict[str, str] = {}
    entry_owners = (
        await db.execute(
            select(WaitlistEntry).where(WaitlistEntry.referral_code.in_(codes))
        )
    ).scalars().all()
    for owner in entry_owners:
        handles[owner.referral_code] = (
            owner.x_username or owner.telegram_username
            or owner.telegram_display_name or ""
        )
    user_owners = (
        await db.execute(select(User).where(User.referral_code.in_(codes)))
    ).scalars().all()
    for owner in user_owners:  # a real User wins over a waitlist row
        handles[owner.referral_code] = owner.x_username or _handle_of(owner)

    use_rows = (
        await db.execute(
            select(WaitlistEntry.referral_code_used, func.count(WaitlistEntry.id))
            .where(WaitlistEntry.referral_code_used.in_(codes))
            .group_by(WaitlistEntry.referral_code_used)
        )
    ).all()
    return handles, {code: int(n) for code, n in use_rows}


async def _reviewer_handles(db, rows) -> dict:
    """admin id -> handle, for the approved/rejected history tabs."""
    ids = {e.approved_by_id for e in rows if e.approved_by_id}
    if not ids:
        return {}
    admins = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {a.id: _handle_of(a) for a in admins}


async def list_entries(
    db,
    *,
    q: str = "",
    status: str = "submitted",
    sort: str = "created",
    direction: str = "asc",
    region: str = "",
    niche: str = "",
    has_score: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Page the review queue.

    Returns ``{items, total, limit, offset}`` — `total` is the count BEFORE
    paging, so the UI can say "1-50 of 213" instead of quietly truncating.
    An empty `status` spans all three states.
    """
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    offset = max(0, int(offset))

    filters = []
    if status:
        if status not in REVIEW_STATUSES:
            raise BadRequest(f"unknown status {status!r}")
        filters.append(WaitlistEntry.status == status)
    if region:
        filters.append(WaitlistEntry.region == region)
    if niche:
        filters.append(WaitlistEntry.niche == niche)
    if has_score is True:
        filters.append(WaitlistEntry.score.is_not(None))
    elif has_score is False:
        filters.append(WaitlistEntry.score.is_(None))

    q = (q or "").strip().lstrip("@")
    if q:
        needle = f"%{q.lower()}%"
        matches = [
            func.lower(WaitlistEntry.x_username).like(needle),
            func.lower(WaitlistEntry.telegram_username).like(needle),
            func.lower(WaitlistEntry.telegram_display_name).like(needle),
            func.lower(WaitlistEntry.referral_code).like(needle),
            func.lower(WaitlistEntry.referral_code_used).like(needle),
        ]
        # a pasted Telegram id is a search too — and for the entries with no
        # username it is the only handle they have
        if q.isdigit():
            matches.append(WaitlistEntry.telegram_id == int(q))
        filters.append(or_(*matches))

    total = (
        await db.execute(select(func.count(WaitlistEntry.id)).where(*filters))
    ).scalar_one()

    column = _SORT_COLUMNS.get(sort, WaitlistEntry.created_at)
    ordering = column.desc() if direction == "desc" else column.asc()
    rows = (
        await db.execute(
            select(WaitlistEntry)
            .where(*filters)
            # unscored applicants sink to the bottom of a score sort in both
            # directions rather than hijacking the top of the descending one
            .order_by(nulls_last(ordering), WaitlistEntry.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    handles, uses = await _referral_context(db, rows)
    reviewers = await _reviewer_handles(db, rows)
    return {
        "items": [
            _entry_row(
                e,
                referrer_handle=handles.get(e.referral_code_used, ""),
                referral_code_uses=uses.get(e.referral_code_used, 0),
                reviewer_handle=reviewers.get(e.approved_by_id, ""),
            )
            for e in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }
