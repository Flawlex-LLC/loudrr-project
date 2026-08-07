import uuid
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import Date, String, Numeric, CheckConstraint, text, BigInteger
from sqlalchemy.orm import mapped_column, Mapped
from app.core.time_utils import utcnow
from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    # identity
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(50), index=True)
    x_username: Mapped[str | None] = mapped_column(String(50), index=True)
    display_name: Mapped[str | None] = mapped_column(String(50))

    # the money — Decimal, never float; precision 12, scale 4 means up to 99999999.9999
    credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), server_default="0"
    )
    total_credits_earned: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), server_default="0"
    )
    total_credits_spent: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), server_default="0"
    )

    # daily caps
    daily_credits_earned: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), server_default="0"
    )
    daily_earned_reset_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )

    # engagement & streak
    total_engagements: Mapped[int] = mapped_column(default=0, server_default="0")
    total_posts: Mapped[int] = mapped_column(default=0, server_default="0")
    current_streak: Mapped[int] = mapped_column(
        default=0, server_default="0", index=True
    )
    # Streak system (Django parity — core/models.py:78-81).
    #   longest_streak         — lifetime max of current_streak (never decreases)
    #   last_engagement_date   — UTC date of the last settled engagement; the
    #                            consecutive-day check compares against this
    #   streak_freeze_available — declared for Django parity; not yet read by
    #                            runtime code, mirroring core/models.py:81
    longest_streak: Mapped[int] = mapped_column(default=0, server_default="0")
    last_engagement_date: Mapped[date | None] = mapped_column(
        Date, default=None, nullable=True
    )
    streak_freeze_available: Mapped[bool] = mapped_column(
        default=True, server_default="true"
    )

    # sponsored XP — separate, non-spendable currency awarded when a user
    # engages with a sponsored post. Three denormalised counters mirror the
    # Django reference (core/models.py:125-127) and let the admin UI render
    # totals without scanning the xp_transactions ledger.
    sponsored_xp: Mapped[int] = mapped_column(default=0, server_default="0")
    total_sponsored_xp_earned: Mapped[int] = mapped_column(
        default=0, server_default="0"
    )
    sponsored_engagements: Mapped[int] = mapped_column(
        default=0, server_default="0"
    )

    # tier & X verification
    tweetscout_score: Mapped[float] = mapped_column(default=0.0, server_default="0")
    tweetscout_last_updated: Mapped[datetime | None] = mapped_column(default=None)
    x_verified: Mapped[bool] = mapped_column(
        default=False, server_default="false", index=True
    )
    x_verified_at: Mapped[datetime | None] = mapped_column(default=None)
    # set during X-OAuth (Ch11) when the connected handle differs from the
    # claimed one — surfaced in /user/ so the frontend can prompt for review
    pending_claimed_x_username: Mapped[str] = mapped_column(
        String(50), default="", server_default=""
    )
    pending_claimed_x_user_id: Mapped[str] = mapped_column(
        String(50), default="", server_default=""
    )

    # honesty (drops on failed verification — Ch13); range 0–50
    honesty_score: Mapped[int] = mapped_column(default=50, server_default="50")

    # access flags
    is_whitelisted: Mapped[bool] = mapped_column(
        default=False, server_default="false", index=True
    )
    loud_access: Mapped[bool] = mapped_column(
        default=False, server_default="false", index=True
    )
    is_banned: Mapped[bool] = mapped_column(default=False, server_default="false")

    # admin RBAC role: "" (regular user) | "admin" | "superadmin". Gates the
    # /api/admin/* endpoints via require_admin/require_superadmin (core/deps.py).
    # Bootstrapped from ADMIN_TELEGRAM_IDS (seed_admins.py).
    role: Mapped[str] = mapped_column(
        String(20), default="", server_default="", index=True
    )

    # referral
    referral_code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "total_credits_earned >= total_credits_spent", name="earned_ge_spent"
        ),
        CheckConstraint("NOT (is_whitelisted AND is_banned)", name="ban_xor_whitelist"),
        CheckConstraint(
            "honesty_score >= 0 AND honesty_score <= 50", name="honesty_score_range"
        ),
        # money invariants — the DB itself refuses a corrupted balance, even if
        # buggy/racing code tried to write one (matches the Django reference,
        # which has credits>=0; we add the totals + daily floors too)
        CheckConstraint("credits >= 0", name="credits_non_negative"),
        CheckConstraint(
            "total_credits_earned >= 0", name="total_earned_non_negative"
        ),
        CheckConstraint("total_credits_spent >= 0", name="total_spent_non_negative"),
        CheckConstraint(
            "daily_credits_earned >= 0", name="daily_earned_non_negative"
        ),
        # RBAC role must be one of the known values
        CheckConstraint(
            "role IN ('', 'admin', 'superadmin')", name="user_role_valid"
        ),
        # XP balance can never go negative — DB-level backstop matching the
        # Django reference (core/models.py:176-178). XPService.admin_revoke
        # clamps to the available balance so this is never hit normally.
        CheckConstraint("sponsored_xp >= 0", name="sponsored_xp_non_negative"),
        # Streak invariants — current_streak never negative, longest never
        # less than current (it's the running max).
        CheckConstraint("current_streak >= 0", name="current_streak_non_negative"),
        CheckConstraint(
            "longest_streak >= current_streak", name="longest_streak_ge_current"
        ),
    )
