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
