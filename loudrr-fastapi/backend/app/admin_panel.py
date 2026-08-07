"""SQLAdmin panel (Ch17) — an instant CRUD admin UI over the models.

FastAPI has no free admin like Django's, so we mount SQLAdmin at /admin with a
separate session-based login (admin auth is NOT the user HMAC auth).

DESIGN — service-backed, not direct DB edits:
  Sensitive operational tables (users, waitlist, posts, x-verification, batches,
  outbox, transactions, audit_log) are READ-ONLY in the panel. Mutations route
  through the service layer via @action buttons (which call services/admin.py,
  services/waitlist.py, services/x_verification.py) so every change is
  audit-logged and runs side effects (outbox events, credit deltas, honesty).

  SiteSettings is the one exception — pure operational config (POST_COST_MIN/
  MAX); admins legitimately edit it inline.

  For ops that need a free-text reason or numeric input (grant/revoke credits,
  reject with notes), use the /api/admin/* JSON endpoints — the panel actions
  cover the happy-path zero-arg flow only.
"""
import logging
import uuid

from sqladmin import Admin, ModelView, action
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.feature_interest import FeatureInterest
from app.models.outbox_event import OutboxEvent
from app.models.post import Post
from app.models.site_setting import SiteSetting
from app.models.transaction import Transaction
from app.models.user import User
from app.models.verification_batch import VerificationBatch
from app.models.waitlist_entry import WaitlistEntry
from app.models.x_verification_request import XVerificationRequest
from app.services import admin as admin_svc
from app.services import waitlist as waitlist_svc
from app.services import x_verification as xverify_svc

logger = logging.getLogger(__name__)


def _pks(request: Request) -> list[uuid.UUID]:
    """Parse the comma-separated ?pks= query SQLAdmin sends on action click."""
    raw = request.query_params.get("pks", "") or ""
    out: list[uuid.UUID] = []
    for piece in raw.split(","):
        s = piece.strip()
        if not s:
            continue
        try:
            out.append(uuid.UUID(s))
        except ValueError:
            logger.warning("admin action: skipping non-UUID pk %r", s)
    return out


async def _panel_actor_id(request: Request) -> uuid.UUID | None:
    """Resolve the User row whose telegram_username matches the admin's session
    login, if any — so panel actions are attributed in audit_logs. Returns
    None for the bootstrap admin (no matching User row), which audit_logs
    accepts (actor_id is nullable)."""
    admin_username = request.session.get("admin")
    if not admin_username:
        return None
    async with SessionLocal() as db:
        u = (
            await db.execute(
                select(User).where(User.telegram_username == admin_username)
            )
        ).scalar_one_or_none()
        return u.id if u else None


def _back_to_list(request: Request, identity: str) -> RedirectResponse:
    """After an action, return the admin to the list page they came from."""
    referer = request.headers.get("Referer")
    if referer:
        return RedirectResponse(referer, status_code=302)
    return RedirectResponse(
        request.url_for("admin:list", identity=identity), status_code=302
    )


class AdminAuth(AuthenticationBackend):
    async def login(self, request) -> bool:
        form = await request.form()
        ok = (
            bool(settings.admin_password)
            and form.get("username") == settings.admin_username
            and form.get("password") == settings.admin_password
        )
        if ok:
            request.session["admin"] = settings.admin_username
        return ok

    async def logout(self, request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request) -> bool:
        return bool(request.session.get("admin"))


# ----------------------------------------------------------------------------
# ModelViews — sensitive tables are read-only; mutations via @action buttons
# ----------------------------------------------------------------------------
class UserAdmin(ModelView, model=User):
    name_plural = "Users"
    column_list = [
        User.id, User.telegram_username, User.x_username, User.credits,
        User.role, User.is_whitelisted, User.is_banned, User.x_verified,
        User.tweetscout_score,
    ]
    column_searchable_list = [User.telegram_username, User.x_username]
    # read-only: ban/unban via the action below; credit/role changes via API
    # endpoints (/api/admin/*) so they go through audited services
    can_create = can_edit = can_delete = False

    @action(
        name="ban",
        label="Ban selected",
        confirmation_message="Ban these users? They will be locked out immediately.",
    )
    async def ban_action(self, request: Request) -> RedirectResponse:
        actor_id = await _panel_actor_id(request)
        async with SessionLocal() as db:
            for pk in _pks(request):
                await admin_svc.ban_user(db, admin_id=actor_id, user_id=pk, reason="panel")
        return _back_to_list(request, self.identity)

    @action(
        name="unban",
        label="Unban selected",
        confirmation_message="Unban these users?",
    )
    async def unban_action(self, request: Request) -> RedirectResponse:
        actor_id = await _panel_actor_id(request)
        async with SessionLocal() as db:
            for pk in _pks(request):
                await admin_svc.unban_user(db, admin_id=actor_id, user_id=pk)
        return _back_to_list(request, self.identity)


class WaitlistAdmin(ModelView, model=WaitlistEntry):
    name_plural = "Waitlist Entries"
    column_list = [
        WaitlistEntry.id, WaitlistEntry.telegram_username, WaitlistEntry.x_username,
        WaitlistEntry.status, WaitlistEntry.created_at,
    ]
    # read-only: approve/reject via the actions below so a User row is created
    # transactionally + the outbox 'waitlist_approved' event is queued
    can_create = can_edit = can_delete = False

    @action(
        name="approve",
        label="Approve selected",
        confirmation_message="Approve these waitlist entries? A User row is created and a Telegram notification is queued.",
    )
    async def approve_action(self, request: Request) -> RedirectResponse:
        actor_id = await _panel_actor_id(request)
        if actor_id is None:
            # waitlist.approve_entry requires a real admin User row for the
            # referrer credit + approved_by_id audit columns
            logger.error("admin panel: approve_action rejected — no actor User row found")
            return _back_to_list(request, self.identity)
        async with SessionLocal() as db:
            for pk in _pks(request):
                await waitlist_svc.approve_entry(db, entry_id=pk, admin_id=actor_id)
        return _back_to_list(request, self.identity)

    @action(
        name="reject",
        label="Reject selected",
        confirmation_message="Reject these waitlist entries? (Use /api/admin/waitlist/{id}/reject/ to include a reason.)",
    )
    async def reject_action(self, request: Request) -> RedirectResponse:
        actor_id = await _panel_actor_id(request)
        if actor_id is None:
            logger.error("admin panel: reject_action rejected — no actor User row found")
            return _back_to_list(request, self.identity)
        async with SessionLocal() as db:
            for pk in _pks(request):
                await waitlist_svc.reject_entry(db, entry_id=pk, admin_id=actor_id, reason="")
        return _back_to_list(request, self.identity)


class TransactionAdmin(ModelView, model=Transaction):
    name_plural = "Transactions"
    column_list = [
        Transaction.id, Transaction.user_id, Transaction.type,
        Transaction.amount, Transaction.balance_after, Transaction.created_at,
    ]
    can_create = can_edit = can_delete = False  # immutable audit trail


class PostAdmin(ModelView, model=Post):
    column_list = [Post.id, Post.user_id, Post.status, Post.escrow, Post.created_at]
    # read-only: posts have escrow + status side effects that must not be
    # direct-edited; status changes happen via settlement workers
    can_create = can_edit = can_delete = False


class XVerificationAdmin(ModelView, model=XVerificationRequest):
    name_plural = "X Verifications"
    column_list = [
        XVerificationRequest.id, XVerificationRequest.user_id,
        XVerificationRequest.claimed_x_username, XVerificationRequest.status,
    ]
    # read-only: approve/reject via the actions below so the user row is
    # updated atomically + clash detection runs
    can_create = can_edit = can_delete = False

    @action(
        name="approve",
        label="Approve selected",
        confirmation_message="Adopt the claimed X handle and mark these users verified?",
    )
    async def approve_action(self, request: Request) -> RedirectResponse:
        actor_id = await _panel_actor_id(request)
        if actor_id is None:
            logger.error("admin panel: x-verification approve rejected — no actor User row")
            return _back_to_list(request, self.identity)
        async with SessionLocal() as db:
            for pk in _pks(request):
                await xverify_svc.approve_x_verification(
                    db, request_id=pk, admin_id=actor_id
                )
        return _back_to_list(request, self.identity)

    @action(
        name="reject",
        label="Reject selected",
        confirmation_message="Reject these X-verification requests? (Use /api/admin/x-verification/{id}/reject/ to include notes.)",
    )
    async def reject_action(self, request: Request) -> RedirectResponse:
        actor_id = await _panel_actor_id(request)
        async with SessionLocal() as db:
            for pk in _pks(request):
                await admin_svc.reject_x_verification(
                    db, admin_id=actor_id, request_id=pk, notes=""
                )
        return _back_to_list(request, self.identity)


class BatchAdmin(ModelView, model=VerificationBatch):
    name_plural = "Verification Batches"
    column_list = [
        VerificationBatch.id, VerificationBatch.user_id, VerificationBatch.status,
        VerificationBatch.credits_awarded,
    ]
    can_create = can_edit = can_delete = False  # status drives credit settlement


class OutboxAdmin(ModelView, model=OutboxEvent):
    name_plural = "Outbox Events"
    column_list = [
        OutboxEvent.id, OutboxEvent.event_type, OutboxEvent.status, OutboxEvent.retry_count,
    ]
    can_create = can_edit = can_delete = False  # the worker owns this table


class SiteSettingAdmin(ModelView, model=SiteSetting):
    name_plural = "Site Settings"
    column_list = [SiteSetting.key, SiteSetting.value, SiteSetting.data_type]
    # editable — operational config (post-cost range etc.) is what admins
    # actually need to tune from the panel


class FeatureInterestAdmin(ModelView, model=FeatureInterest):
    name_plural = "Feature Interests"
    column_list = [FeatureInterest.id, FeatureInterest.user_id, FeatureInterest.feature]
    can_create = can_edit = can_delete = False  # user-generated opt-in data


class AuditLogAdmin(ModelView, model=AuditLog):
    name_plural = "Audit Log"
    column_list = [
        AuditLog.id, AuditLog.actor_id, AuditLog.action,
        AuditLog.target_type, AuditLog.target_id, AuditLog.created_at,
    ]
    can_create = can_edit = can_delete = False  # append-only


def mount_admin(app) -> Admin:
    admin = Admin(
        app, engine,
        authentication_backend=AdminAuth(secret_key=settings.secret_key),
    )
    for view in (
        UserAdmin, WaitlistAdmin, TransactionAdmin, PostAdmin, XVerificationAdmin,
        BatchAdmin, OutboxAdmin, SiteSettingAdmin, FeatureInterestAdmin, AuditLogAdmin,
    ):
        admin.add_view(view)
    return admin
