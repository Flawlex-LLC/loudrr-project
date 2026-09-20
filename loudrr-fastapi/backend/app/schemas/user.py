from typing import Literal

from pydantic import BaseModel


# ---- requests ----
class LinkXRequest(BaseModel):
    # accepted loosely (may include a leading @ / whitespace); the service
    # normalizes and validates against ^[a-zA-Z0-9_]{1,15}$, raising a 400
    # (not a 422) so the error matches the frontend's {"error": ...} contract.
    x_username: str


# ---- /user/ ----
class UserInfoResponse(BaseModel):
    id: str
    display_name: str | None
    telegram_username: str | None
    x_username: str | None
    credits: float
    daily_earned: float
    daily_cap: int
    total_engagements: int
    tier: str
    current_streak: int
    longest_streak: int
    streak_multiplier: float
    streak_next_milestone: int | None
    tweetscout_score: float
    tweetscout_last_updated: str | None
    honesty_score: int
    available_posts: int
    engaged_today: int
    is_whitelisted: bool
    loud_access: bool
    x_verified: bool
    pending_claimed_x_username: str | None
    x_verification_pending_review: bool


# ---- /user/stats/ ----
class UserStatsUser(BaseModel):
    display_name: str | None
    telegram_username: str | None
    credits: float
    tier: str
    current_streak: int
    longest_streak: int
    streak_multiplier: float
    streak_next_milestone: int | None
    total_credits_earned: float
    total_credits_spent: float


class PostStats(BaseModel):
    total: int
    active: int
    completed: int


class EngagementStats(BaseModel):
    given: int
    received: int


class RecentPost(BaseModel):
    id: str
    x_link: str
    status: str
    escrow_remaining: float
    engagement_progress: int
    created_at: str


class UserStatsResponse(BaseModel):
    user: UserStatsUser
    posts: PostStats
    engagements: EngagementStats
    recent_posts: list[RecentPost]


# ---- /user/link-x/ ----
class LinkXResponse(BaseModel):
    success: bool
    x_username: str
    tweetscout_score: float
    tier: str
    followers_count: int
    display_name: str


# ---- /user/waitlist-enrichment/ ----
class WaitlistEnrichmentResponse(BaseModel):
    # The X handle we resolved for the caller (from User row, else WaitlistEntry).
    # The frontend uses this to guard against painting the caller's score onto
    # some other handle when the page is opened with ?u=someone_else.
    x_username: str | None
    score: float | None
    tier: str | None
    followers: list[str]  # bare X usernames, no '@', up to 10
    # total smart followers when the provider knows it (>= len(followers)),
    # else len(followers); 0 when followers is empty
    followers_count: int
    # "pending" = the sign-up fetch hasn't landed yet; "ready"; "not_found" =
    # fetched, but the provider has no score for this account (yet)
    score_status: Literal["pending", "ready", "not_found"] = "not_found"
    score_updated_at: str | None = None  # last finished fetch, ISO


# ---- POST /user/refresh-score/ ----
class RefreshScoreResponse(WaitlistEnrichmentResponse):
    # "updated" | "not_found" (no score from the provider; old one kept) |
    # "cooldown" (refreshed recently — retry_after_seconds) | "unavailable"
    # (provider backing off; nothing recorded, try later)
    result: Literal["updated", "not_found", "cooldown", "unavailable"]
    retry_after_seconds: int = 0
