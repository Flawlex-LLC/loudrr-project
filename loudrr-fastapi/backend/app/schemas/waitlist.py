from pydantic import BaseModel
import enum
from app.schemas._types import NonEmptyStr, ShortText, OptCode, OptShort
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
    x_link: NonEmptyStr
    referral_code: OptCode = None
    region: Region | None = None
    niche: Niche | None = None
    other_platforms: list[OtherPlatform] | None = None


class WaitlistRegisterResponse(BaseModel):
    status: str
    message: str
    x_username: str
    referral_code: str
