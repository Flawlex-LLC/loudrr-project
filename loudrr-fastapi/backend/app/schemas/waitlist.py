from pydantic import BaseModel
import enum
from app.schemas._types import ShortText, OptCode, OptShort, OAuthProof
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
    referral_code: OptCode = None
    region: Region | None = None
    niche: Niche | None = None
    other_platforms: list[OtherPlatform] | None = None


class WaitlistRegisterResponse(BaseModel):
    status: str
    message: str
    x_username: str
    referral_code: str
