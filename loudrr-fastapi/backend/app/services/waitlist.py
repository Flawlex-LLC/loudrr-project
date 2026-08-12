import logging
import secrets
import uuid
from dataclasses import dataclass
from sqlalchemy import func

from app.core.errors import BadRequest, Conflict
from app.core.time_utils import utcnow
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.repositories.user import UserRepository
from app.repositories.waitlist import WaitlistRepository
from app.services.waitlist_x_oauth import verify_and_extract

logger = logging.getLogger(__name__)


@dataclass
class RegisterResult:
    entry: WaitlistEntry
    was_new: bool


@dataclass
class StatusResult:
    status: str  # approved | waitlisted | not_registered
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


async def _resolve_referrer(
    users: UserRepository, waitlist: WaitlistRepository, code: str | None
):
    """Return (referrer_id, code_used). Silently ignores a bad code."""
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


async def _load_submitted(
    waitlist: WaitlistRepository, entry_id: uuid.UUID, action: str
) -> WaitlistEntry:
    """Load an entry that must still be 'submitted', or refuse the action.
    Shared by approve_entry and reject_entry — the state-machine guard."""
    entry = await waitlist.get_or_404(id=entry_id, label="waitlist entry")
    if entry.status != "submitted":
        raise Conflict(f"Entry is {entry.status!r}, cannot {action}")
    return entry


# ---- THE USE CASES ----
async def register_entry(db, *, tg_user: dict, payload) -> RegisterResult:
    """Register a waitlist entry. Idempotent on telegram_id."""
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise BadRequest("Missing Telegram ID")

    # X OAuth is now mandatory step 1 of the waitlist flow. The proof is a
    # signed itsdangerous token minted by the callback after the applicant
    # completes X OAuth — verify_and_extract re-checks signature, iat freshness,
    # and cross-references the embedded tg_id against Telegram initData.
    x_username, x_user_id = verify_and_extract(
        payload.x_proof, telegram_id=telegram_id,
    )

    users = UserRepository(db)
    waitlist = WaitlistRepository(db)

    # idempotency on telegram_id — same user submitting twice is success
    existing = await waitlist.get(telegram_id=telegram_id)
    if existing:
        return RegisterResult(entry=existing, was_new=False)

    xu = x_username.lower()
    # case-insensitive checks need a raw filter, not filter_by
    if await waitlist.exists_where(func.lower(WaitlistEntry.x_username) == xu):
        raise BadRequest("X username already registered")
    if await users.exists_where(func.lower(User.x_username) == xu):
        raise BadRequest("X username already in use")

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
    await db.commit()
    logger.info("waitlist entry created: %s", entry.id)
    return RegisterResult(entry=entry, was_new=True)


async def get_status(db, *, telegram_id: int) -> StatusResult:
    """approved (a User row exists), waitlisted, or not_registered."""
    if await UserRepository(db).exists(telegram_id=telegram_id):
        return StatusResult(status="approved")
    entry = await WaitlistRepository(db).get(telegram_id=telegram_id)
    if entry is not None:
        return StatusResult(status="waitlisted", entry=entry)
    return StatusResult(status="not_registered")


async def approve_entry(
    db, *, entry_id: uuid.UUID, admin_id: uuid.UUID
) -> User:
    """Approve a submitted entry: create the User, and bump the referrer's
    total_referrals under a row lock — all in one transaction."""
    from app.core.db_helpers import locked_row

    waitlist = WaitlistRepository(db)
    users = UserRepository(db)

    # load it + enforce the "must be submitted" guard in one call
    entry = await _load_submitted(waitlist, entry_id, "approve")
    entry.status = "approved"
    entry.approved_at = utcnow()
    entry.approved_by_id = admin_id

    user = await users.create(
        telegram_id=entry.telegram_id,
        telegram_username=entry.telegram_username,
        x_username=entry.x_username,
        display_name=entry.telegram_display_name,
        referral_code=entry.referral_code,  # keep their existing code
    )
    entry.created_user_id = user.id

    if entry.referrer_id:
        # lock the referrer row first: two approvals of the same referrer's
        # referees at once must not both read-then-write the old count
        async with locked_row(db, User, id=entry.referrer_id) as referrer:
            referrer.total_referrals += 1
    # queue the "waitlist_approved" Telegram card in THIS transaction
    from app.services.outbox import OutboxService
    await OutboxService.queue_waitlist_approved(
        db, entry_id=entry.id, telegram_id=entry.telegram_id, x_username=entry.x_username,
    )
    await db.commit()
    # Parity with Django (core/admin.py:741) — kick off the TweetScout fetch
    # for the freshly created User so their tier multiplier is populated soon
    # after approval. Best-effort: arq enqueue runs post-commit, never blocks
    # the response. With redis/use_task_queue unset (tests/dev), enqueue() is
    # a no-op — the user can still be backfilled manually via /onboarding/.
    from app.tasks.enqueue import enqueue
    await enqueue("fetch_tweetscout_for_user", str(user.id))
    return user


async def reject_entry(
    db, *, entry_id: uuid.UUID, admin_id: uuid.UUID, reason: str = ""
) -> WaitlistEntry:
    waitlist = WaitlistRepository(db)
    # the same shared guard — load it, or refuse if it isn't "submitted"
    entry = await _load_submitted(waitlist, entry_id, "reject")
    entry.status = "rejected"
    entry.rejection_reason = reason
    entry.approved_by_id = admin_id
    entry.approved_at = utcnow()
    # queue the "waitlist_rejected" Telegram card in THIS transaction
    from app.services.outbox import OutboxService
    await OutboxService.queue_waitlist_rejected(
        db, entry_id=entry.id, telegram_id=entry.telegram_id,
        x_username=entry.x_username, reason=reason,
    )
    await db.commit()
    return entry
