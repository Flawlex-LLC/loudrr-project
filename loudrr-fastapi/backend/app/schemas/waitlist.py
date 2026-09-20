from typing import Literal

from pydantic import BaseModel, Field
import enum
from app.schemas._types import ShortText, OptShort, OAuthProof
from app.models.waitlist_entry import Region, Niche


class OtherPlatformKind(str, enum.Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    OTHER = "other"


class OtherPlatform(BaseModel):
    platform: OtherPlatformKind
    username: ShortText
    platform_name: OptShort = None


class WaitlistRegisterRequest(BaseModel):
    # Telegram-only signup — no email collection. telegram_id comes from the
    # signed initData header (set by verify_init_data at deps.py), not the
    # body, so it's not on this model.
    #
    # x_proof is a signed itsdangerous token minted by the /api/auth/x/callback/
    # waitlist/ handler after the applicant completes X OAuth. It carries the
    # verified handle + numeric X user id; the register endpoint re-verifies
    # signature + iat freshness and cross-checks the embedded tg_id against
    # Telegram initData before trusting it.
    x_proof: OAuthProof
    # Lenient on purpose: a mangled ?startapp=ref_… link must not 422 the whole
    # sign-up. The service normalizes it and silently ignores unknown codes.
    referral_code: str | None = Field(default=None, max_length=64)
    region: Region | None = None
    niche: Niche | None = None
    # the UI offers three platforms; the service keeps at most 5
    other_platforms: list[OtherPlatform] | None = Field(default=None, max_length=5)


class WaitlistRegisterResponse(BaseModel):
    status: str
    message: str
    x_username: str
    referral_code: str


class OAuthConfirmInfoResponse(BaseModel):
    """GET /waitlist/x-oauth/confirm/{token}/: what the browser confirmation
    page shows before the X account owner decides."""
    x_username: str
    telegram_label: str   # the Telegram account that started Connect X
    expires_in: int       # seconds left to decide


class OAuthConfirmLookupRequest(BaseModel):
    # the one-time token from the callback redirect; in the body so it never
    # reaches request logs, Sentry or browser history
    token: str = Field(min_length=1, max_length=128)


class OAuthConfirmDecisionRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    decision: Literal["confirm", "cancel"]


class OAuthConfirmDecisionResponse(BaseModel):
    ok: bool
    status: Literal["confirmed", "cancelled"]


class PublicCardResponse(BaseModel):
    """GET /waitlist/card/{username}/ — what the public share card shows."""
    x_username: str
    score: float | None
    tier: str | None
    followers: list[str]
    followers_count: int
    referral_code: str
