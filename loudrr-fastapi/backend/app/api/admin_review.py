"""The two admin review queues — waitlist and X verification.

Split out of api/admin.py because these are the pages the team actually spends
its day in, and they grew past "list the first 200 rows": search, sort,
filters, paging, history tabs and the two recovery actions (reopen a
rejection, refresh a missing score) belong together and next to each other.

The router carries no prefix of its own — api/admin.py includes it into the
`/api/admin` router, so every path below is `/api/admin/…` and inherits the
same auth story. Every route is `require_admin`; each write goes through the
audited service layer and never touches the DB directly.
"""
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.deps import require_admin
from app.db.session import get_session
from app.models.user import User
from app.models.waitlist_entry import Niche, Region
from app.models.waitlist_entry import WaitlistStatus
from app.models.x_verification_request import XVerificationStatus
from app.services import waitlist as waitlist_svc
from app.services import x_verification as xverify_svc

router = APIRouter(tags=["admin"])


# ---- request bodies ----
class ReopenBody(BaseModel):
    """Reopening is an admin-to-admin act — there is no public half. The note
    is for whoever wonders later why a rejected applicant is pending again."""

    internal_note: str = Field(default="", max_length=1000)


# ---- waitlist review queue ----
@router.get("/waitlist/")
async def list_waitlist(
    q: str = Query(default="", max_length=100, description=(
        "Case-insensitive substring over X handle, Telegram @username, "
        "Telegram display name and either referral code. An all-digits q also "
        "matches telegram_id exactly."
    )),
    status: str = Query(default=WaitlistStatus.SUBMITTED.value, description=(
        "submitted (the queue) | approved | rejected (history). "
        "Empty string spans all three."
    )),
    sort: str = Query(default="created", pattern="^(created|score|tier)$"),
    direction: str = Query(default="asc", alias="dir", pattern="^(asc|desc)$"),
    region: str = Query(default="", max_length=30),
    niche: str = Query(default="", max_length=20),
    has_score: bool | None = Query(default=None, description=(
        "true = only scored applicants, false = only the unscored ones "
        "(the queue the Refresh score button exists for)"
    )),
    limit: int = Query(default=50, ge=1, le=waitlist_svc.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """`{items, total, limit, offset}`.

    `total` counts every row matching the filters, before paging — the old
    endpoint returned a bare list capped at 200 with no count, so applicant
    201 simply wasn't there and nothing on screen said so.
    """
    return await waitlist_svc.list_entries(
        db, q=q, status=status, sort=sort, direction=direction,
        region=region, niche=niche, has_score=has_score,
        limit=limit, offset=offset,
    )


@router.get("/waitlist/facets/")
async def waitlist_facets(_admin: User = Depends(require_admin)):
    """The allowed values behind the region / niche filters.

    Served from the model's own enums so the dropdowns can't drift from the
    CHECK constraints (the UI owns the human labels — "cis_eastern_europe" is
    a database value, not a thing to show a person).
    """
    return {
        "regions": [r.value for r in Region],
        "niches": [n.value for n in Niche],
        "statuses": list(waitlist_svc.REVIEW_STATUSES),
    }


@router.post("/waitlist/{entry_id}/reopen/")
async def reopen_waitlist(
    entry_id: uuid.UUID,
    body: ReopenBody,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Undo a rejection — put the entry back in the queue.

    409 when the entry isn't `rejected`, or when it already created a User.
    Audited as `reopen_waitlist`. No Telegram message is sent.
    """
    entry = await waitlist_svc.reopen_entry(
        db, entry_id=entry_id, admin_id=admin.id, internal_note=body.internal_note,
    )
    return {"ok": True, "entry_id": str(entry.id), "status": entry.status}


@router.post("/waitlist/{entry_id}/refresh-score/")
async def refresh_waitlist_score(
    entry_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """Re-run the score fetch for one applicant.

    `queued=true` means the arq job was accepted and the row will update in
    the background (poll the list). `queued=false` means no queue is
    configured and the fetch ran inline — `result` is then the job's own
    outcome: found | not_found | unavailable | skipped.
    """
    outcome = await waitlist_svc.admin_refresh_score(
        db, entry_id=entry_id, admin_id=admin.id,
    )
    return {"ok": True, **outcome}


# ---- X verification review queue ----
@router.get("/x-verification/")
async def list_x_verifications(
    q: str = Query(default="", max_length=100, description=(
        "Case-insensitive substring over the submitted handle, the claimed "
        "handle, and the user's Telegram/X usernames."
    )),
    status: str = Query(default=XVerificationStatus.PENDING.value, description=(
        "PENDING | APPROVED | REJECTED. Empty string spans all three."
    )),
    limit: int = Query(default=50, ge=1, le=xverify_svc.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db=Depends(get_session),
):
    """`{items, total, limit, offset}`, each item carrying the context the
    identity call actually needs: the user's standing (score/tier/credits/
    banned/verified/joined), the claimed account's numeric X id, every prior
    request that user made with the reviewer's note on it, and — when the
    handle they want already belongs to somebody else —
    `claimed_handle_taken_by`, which is precisely the case approve answers
    with a 409.
    """
    return await xverify_svc.list_requests(
        db, q=q, status=status, limit=limit, offset=offset,
    )
