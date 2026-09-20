"""Single source of truth for site-settings metadata: groups, defaults,
types, descriptions, bounds, and whether each setting is currently READ by
backend code at runtime (`live=True`) vs just persisted for future wiring
(`live=False`). The admin UI uses the groups for sectioning; seed_settings
uses (key, default, data_type, description) for upserts.

Bounds (`min`/`max`/`step`) are enforced SERVER-SIDE by the PUT endpoint and
mirrored into the admin inputs so a negative cooldown or a 15-digit karma
cost can never be saved. `danger=True` marks a setting whose change moves
real money (or turns a feature off for everyone) — the admin UI routes those
through a confirm step showing old → new plus `impact`.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: str
    data_type: str  # "int" | "float" | "decimal" | "bool" | "str"
    description: str
    live: bool      # True if backend code reads this setting today
    # numeric bounds — None means "unbounded on that side". `step` is a UI
    # hint (input step attribute); `unit` is a short suffix shown next to the
    # field ("karma", "seconds", …).
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    # money-critical / availability-critical → confirm before saving
    danger: bool = False
    impact: str = ""  # one-line "what this actually does" for the confirm dialog


@dataclass(frozen=True)
class SettingGroup:
    name: str
    description: str
    settings: tuple[SettingSpec, ...]


ALL_GROUPS: tuple[SettingGroup, ...] = (
    SettingGroup(
        name="Kill switches",
        description=(
            "Emergency stops. Turning one OFF makes the matching write path "
            "refuse new work with a user-facing message — nothing already in "
            "flight is cancelled or refunded."
        ),
        settings=(
            SettingSpec(
                "MAINTENANCE_MODE", "false", "bool",
                "Master stop. While ON, posts, claims, waitlist sign-ups and sponsor ingestion all refuse.",
                live=True, danger=True,
                impact="Every gated write path refuses for every user until this is turned back off.",
            ),
            SettingSpec(
                "POSTS_ENABLED", "true", "bool",
                "Allow new posts to be submitted (existing posts keep running).",
                live=True, danger=True,
                impact="OFF: /posts/submit/ refuses; no new karma goes into escrow.",
            ),
            SettingSpec(
                "CLAIMS_ENABLED", "true", "bool",
                "Allow users to queue a verification claim (settlement of already-queued batches continues).",
                live=True, danger=True,
                impact="OFF: nobody can start a new claim, so no new karma is paid out.",
            ),
            SettingSpec(
                "WAITLIST_REGISTRATION_ENABLED", "true", "bool",
                "Accept new waitlist registrations.",
                live=True, danger=True,
                impact="OFF: /waitlist/register/ refuses; the landing page stops converting.",
            ),
            SettingSpec(
                "SPONSOR_INGEST_ENABLED", "true", "bool",
                "Turn monitored sponsor tweets into sponsored raid posts.",
                live=True, danger=True,
                impact="OFF: the sponsor stream keeps polling but creates no sponsored posts.",
            ),
        ),
    ),
    SettingGroup(
        name="Economy",
        description="Core karma cost & earn rates",
        settings=(
            SettingSpec("POST_COST", "80", "int", "Default karma stake when submitting a post", live=True,
                        min=1, max=1_000_000, step=1, unit="karma", danger=True,
                        impact="Changes the default escrow every new post locks up."),
            SettingSpec("POST_COST_MIN", "10", "int", "Minimum karma stake (lower bound on submit form)", live=True,
                        min=1, max=1_000_000, step=1, unit="karma", danger=True,
                        impact="Raises/lowers the floor on what a poster must stake."),
            SettingSpec("POST_COST_MAX", "200", "int", "Maximum karma stake (upper bound on submit form)", live=True,
                        min=1, max=1_000_000, step=1, unit="karma", danger=True,
                        impact="Raises/lowers the ceiling on what a poster can stake."),
            SettingSpec("CREDIT_PER_ENGAGEMENT", "1", "int", "Base karma awarded per verified engagement (multiplied by tier)", live=True,
                        min=0, max=10_000, step=1, unit="karma", danger=True,
                        impact="Multiplies every future payout — the single biggest lever on karma inflation."),
            SettingSpec("DAILY_EARN_CAP", "160", "int", "Per-user daily karma earning ceiling", live=True,
                        min=0, max=1_000_000, step=1, unit="karma/day", danger=True,
                        impact="Caps how much karma one account can earn per UTC day."),
            SettingSpec("ENGAGEMENT_COOLDOWN", "0", "int", "Cooldown seconds between engagements by the same user (0 = disabled)", live=True,
                        min=0, max=86_400, step=1, unit="seconds"),
        ),
    ),
    SettingGroup(
        name="Verification & anti-gaming",
        description="Controls how strictly engagements are verified before crediting",
        settings=(
            SettingSpec("MIN_ENGAGEMENTS_TO_CLAIM", "10", "int", "How many pending engagements a user needs before /session/complete/", live=True,
                        min=1, max=1_000, step=1, unit="engagements"),
            SettingSpec("MIN_SESSION_DURATION_SECONDS", "150", "int", "Anti-gaming: required seconds between first click and claim", live=True,
                        min=0, max=86_400, step=1, unit="seconds"),
            SettingSpec("POST_EXPIRY_HOURS", "48", "int", "Hours after which an active post auto-expires", live=True,
                        min=1, max=8_760, step=1, unit="hours"),
            SettingSpec("AUDIT_PROBABILITY", "1.0", "float", "Probability per engagement of running the full TwitterAPI check (rest trusted-pass). 1.0 = verify every engagement.", live=True,
                        min=0, max=1, step=0.01, danger=True,
                        impact="Below 1.0 a share of engagements are trusted-passed without a Twitter check."),
            SettingSpec("VERIFICATION_BATCH_SIZE", "0", "int", "Max engagements per verification batch (0 = no cap, take all pending)", live=True,
                        min=0, max=10_000, step=1, unit="engagements"),
            SettingSpec("VERIFICATION_SAMPLE_SIZE", "0", "int", "Per-batch cap on items audited against the Twitter API (0 = no cap, audit every item the probability roll picked)", live=True,
                        min=0, max=10_000, step=1, unit="engagements"),
            SettingSpec("MAX_VERIFICATION_RETRIES", "0", "int", "Additional attempts on transient Twitter API failures (network errors / 5xx). 0 = single attempt.", live=True,
                        min=0, max=10, step=1, unit="retries"),
        ),
    ),
    SettingGroup(
        name="Tier thresholds (TweetScout score)",
        description="Score breakpoints that determine which karma tier a user falls into",
        settings=(
            SettingSpec("TIER_NORMIE_THRESHOLD", "100", "int", "TweetScout score ≥ this to reach Normie", live=True,
                        min=1, max=1_000_000, step=1, unit="score", danger=True,
                        impact="Moves users between tiers — and therefore between karma multipliers."),
            SettingSpec("TIER_DEGEN_THRESHOLD", "200", "int", "…Degen", live=True,
                        min=1, max=1_000_000, step=1, unit="score", danger=True,
                        impact="Moves users between tiers — and therefore between karma multipliers."),
            SettingSpec("TIER_BASED_THRESHOLD", "400", "int", "…Based", live=True,
                        min=1, max=1_000_000, step=1, unit="score", danger=True,
                        impact="Moves users between tiers — and therefore between karma multipliers."),
            SettingSpec("TIER_LEGEND_THRESHOLD", "600", "int", "…Legend", live=True,
                        min=1, max=1_000_000, step=1, unit="score", danger=True,
                        impact="Moves users between tiers — and therefore between karma multipliers."),
            SettingSpec("TIER_OG_THRESHOLD", "800", "int", "…OG", live=True,
                        min=1, max=1_000_000, step=1, unit="score", danger=True,
                        impact="Moves users between tiers — and therefore between karma multipliers."),
            SettingSpec("TIER_GOAT_THRESHOLD", "1000", "int", "…GOAT (top tier)", live=True,
                        min=1, max=1_000_000, step=1, unit="score", danger=True,
                        impact="Moves users between tiers — and therefore between karma multipliers."),
        ),
    ),
    SettingGroup(
        name="Tier multipliers",
        description="Karma multiplier applied per tier on every earn",
        settings=(
            SettingSpec("TIER_ANON_MULTIPLIER", "1.00", "decimal", "Anon — base, no boost", live=True,
                        min=1, max=10, step=0.01, unit="×", danger=True,
                        impact="Every future earn by this tier is multiplied by this number."),
            SettingSpec("TIER_NORMIE_MULTIPLIER", "1.10", "decimal", "Normie", live=True,
                        min=1, max=10, step=0.01, unit="×", danger=True,
                        impact="Every future earn by this tier is multiplied by this number."),
            SettingSpec("TIER_DEGEN_MULTIPLIER", "1.15", "decimal", "Degen", live=True,
                        min=1, max=10, step=0.01, unit="×", danger=True,
                        impact="Every future earn by this tier is multiplied by this number."),
            SettingSpec("TIER_BASED_MULTIPLIER", "1.20", "decimal", "Based", live=True,
                        min=1, max=10, step=0.01, unit="×", danger=True,
                        impact="Every future earn by this tier is multiplied by this number."),
            SettingSpec("TIER_LEGEND_MULTIPLIER", "1.25", "decimal", "Legend", live=True,
                        min=1, max=10, step=0.01, unit="×", danger=True,
                        impact="Every future earn by this tier is multiplied by this number."),
            SettingSpec("TIER_OG_MULTIPLIER", "1.30", "decimal", "OG", live=True,
                        min=1, max=10, step=0.01, unit="×", danger=True,
                        impact="Every future earn by this tier is multiplied by this number."),
            SettingSpec("TIER_GOAT_MULTIPLIER", "1.35", "decimal", "GOAT", live=True,
                        min=1, max=10, step=0.01, unit="×", danger=True,
                        impact="Every future earn by this tier is multiplied by this number."),
        ),
    ),
    SettingGroup(
        name="Streaks",
        description="Bonuses for consecutive daily activity",
        settings=(
            SettingSpec("STREAK_7_DAY_MULTIPLIER", "1.0", "decimal", "Extra multiplier at a 7-day streak", live=True,
                        min=1, max=10, step=0.01, unit="×"),
            SettingSpec("STREAK_7_DAY_BONUS", "5", "int", "Flat karma bonus at 7-day streak", live=True,
                        min=0, max=100_000, step=1, unit="karma"),
            SettingSpec("STREAK_14_DAY_MULTIPLIER", "1.0", "decimal", "Extra multiplier at 14-day streak", live=True,
                        min=1, max=10, step=0.01, unit="×"),
            SettingSpec("STREAK_14_DAY_BONUS", "6", "int", "Flat karma bonus at 14-day streak", live=True,
                        min=0, max=100_000, step=1, unit="karma"),
            SettingSpec("STREAK_30_DAY_MULTIPLIER", "1.0", "decimal", "Extra multiplier at 30-day streak", live=True,
                        min=1, max=10, step=0.01, unit="×"),
            SettingSpec("STREAK_30_DAY_BONUS", "10", "int", "Flat karma bonus at 30-day streak", live=True,
                        min=0, max=100_000, step=1, unit="karma"),
        ),
    ),
    SettingGroup(
        name="Sponsored posts",
        description="XP awarded on sponsored-post engagements (no karma cost)",
        settings=(
            SettingSpec("SPONSORED_XP_PER_ENGAGEMENT", "5", "int", "XP per sponsored-post engagement", live=True,
                        min=0, max=100_000, step=1, unit="XP"),
        ),
    ),
    SettingGroup(
        name="Scores",
        description="Scores are fetched at sign-up and when a user taps Refresh score in the mini-app — never on a schedule",
        settings=(
            SettingSpec("SCORE_REFRESH_COOLDOWN_MINUTES", "60", "int", "Minimum minutes between two Refresh score taps for the same account (0 = no cooldown). Each tap costs ~2 provider requests.", live=True,
                        min=0, max=10_080, step=1, unit="minutes"),
        ),
    ),
    SettingGroup(
        name="Karma decay",
        description="Inactive-user karma decay policy",
        settings=(
            SettingSpec("KARMA_DECAY_THRESHOLD_DAYS", "14", "int", "Days of inactivity before decay kicks in", live=True,
                        min=1, max=3_650, step=1, unit="days", danger=True,
                        impact="Lowering it starts burning karma off idle accounts sooner."),
            SettingSpec("KARMA_DECAY_RATE", "0.015", "float", "Daily fraction of karma decayed once over threshold", live=True,
                        min=0, max=1, step=0.001, danger=True,
                        impact="Fraction of an idle user's balance destroyed per day. 0.015 = 1.5%/day."),
        ),
    ),
    SettingGroup(
        name="Telegram message templates",
        description=(
            "Text shown in the Telegram cards sent to users by the outbox. "
            "Supports {x_username_part} placeholder (rendered as ', @handle' "
            "if known, otherwise empty)."
        ),
        settings=(
            SettingSpec(
                "TG_MSG_WAITLIST_SUBMITTED",
                "🎉 You are on the Loudrr waitlist{x_username_part}! We will message you the moment you are approved.",
                "str",
                "Telegram message sent when a user joins the waitlist.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_WAITLIST_APPROVED",
                "✅ You are in! Your Loudrr access is approved{x_username_part}. Open the app to start earning karma.",
                "str",
                "Telegram message sent when a user is approved off the waitlist.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_WAITLIST_REJECTED",
                "Your Loudrr waitlist application{x_username_part} was not approved at this time.{reason_part}",
                "str",
                "Telegram message sent when an admin rejects a waitlist entry. "
                "{reason_part} renders the reason YOU type (with its label) and "
                "disappears when you leave it blank — the internal note is never sent.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_X_VERIFICATION_APPROVED",
                "✅ Your X account{x_username_part} is verified. You can now earn karma on Loudrr.",
                "str",
                "Telegram message sent when an admin approves an X-verification request.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_X_VERIFICATION_REJECTED",
                "Your X verification request was rejected. Submitted: @{submitted_x_username}, Claimed: @{claimed_x_username}.{note_part}",
                "str",
                "Telegram message sent when an admin rejects an X-verification request.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_ADMIN_GRANT_CREDITS",
                "An admin granted you {amount} karma.{description_part}",
                "str",
                "Telegram message sent when an admin grants credits to a user.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_ADMIN_REVOKE_CREDITS",
                "{amount} karma was deducted from your balance.{reason_part}",
                "str",
                "Telegram message sent when an admin revokes credits from a user.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_ADMIN_BAN",
                "Your Loudrr account has been suspended.{reason_part}",
                "str",
                "Telegram message sent when an admin bans a user.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_DAILY_CAP_REACHED",
                "You hit today's earning cap ({cap} karma). It resets at 00:00 UTC — see you tomorrow.",
                "str",
                "Telegram message sent (once per UTC day) when a user can't earn more credits today.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_CLAIM_COMPLETED",
                "Claim settled: earned {awarded} karma from {passed} engagements ({failed} failed verification).",
                "str",
                "Telegram message sent when a verification batch finishes settling.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_POST_COMPLETED",
                "Your post is complete — {total_engagements} engagements delivered. Escrow fully paid out.",
                "str",
                "Telegram message sent to the poster when their post's escrow is fully paid out.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_POST_EXPIRED",
                "Your post expired and {refund_amount} karma was refunded to your balance.",
                "str",
                "Telegram message sent to the poster when their post expires and is refunded.",
                live=True,
            ),
            SettingSpec(
                "TG_MSG_STREAK_MILESTONE",
                "🔥 Day {streak} streak! +{bonus} bonus karma deposited.",
                "str",
                "Telegram message sent when a user crosses a 7/14/30-day streak milestone.",
                live=True,
            ),
        ),
    ),
)


def all_specs():
    """Flatten all groups to a single (group_name, SettingSpec) sequence."""
    for g in ALL_GROUPS:
        for s in g.settings:
            yield g.name, s


def spec_by_key(key: str) -> SettingSpec | None:
    for _g, s in all_specs():
        if s.key == key:
            return s
    return None


# ---------------------------------------------------------------------------
# Validation — the PUT endpoint's only source of truth for what "valid" means
# ---------------------------------------------------------------------------
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

# Tier bands, weakest → strongest. ANON is the implicit floor (score 0), so it
# has no threshold; the rest must be strictly increasing or `tier_for` would
# make a band unreachable.
TIER_ORDER: tuple[str, ...] = ("NORMIE", "DEGEN", "BASED", "LEGEND", "OG", "GOAT")
MULTIPLIER_TIERS: tuple[str, ...] = ("ANON",) + TIER_ORDER

MAX_VALUE_LENGTH = 255


def coerce_value(value: str, data_type: str):
    """Coerce a raw string per data_type. Raises ValueError if it doesn't fit."""
    if data_type == "int":
        # int("1e9") and int("1.5") both raise — deliberate, those are not ints
        return int(value)
    if data_type == "float":
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
            raise ValueError(f"{value!r} is not a finite number")
        return f
    if data_type == "decimal":
        try:
            d = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"{value!r} is not a valid decimal")
        if not d.is_finite():
            raise ValueError(f"{value!r} is not a finite decimal")
        return d
    if data_type == "bool":
        norm = value.strip().lower()
        if norm not in _TRUTHY | _FALSY:
            raise ValueError(f"{value!r} is not a valid bool")
        return norm in _TRUTHY
    if data_type == "str":
        return value
    raise ValueError(f"unknown data_type {data_type!r}")


def validate_value(spec: SettingSpec, raw: str):
    """Coerce `raw` per the spec AND enforce its min/max. Returns the coerced
    value. Raises ValueError with a message meant for the admin's screen."""
    if len(raw) > MAX_VALUE_LENGTH:
        raise ValueError(f"value too long (max {MAX_VALUE_LENGTH} chars)")
    try:
        value = coerce_value(raw, spec.data_type)
    except (ValueError, ArithmeticError) as e:
        raise ValueError(f"value does not coerce to {spec.data_type}: {e}")

    if spec.data_type in ("int", "float", "decimal"):
        numeric = float(value)
        unit = f" {spec.unit}" if spec.unit else ""
        if spec.min is not None and numeric < spec.min:
            raise ValueError(
                f"{spec.key} must be ≥ {_fmt(spec.min)}{unit} (got {raw})"
            )
        if spec.max is not None and numeric > spec.max:
            raise ValueError(
                f"{spec.key} must be ≤ {_fmt(spec.max)}{unit} (got {raw})"
            )
    return value


def _fmt(n: float) -> str:
    """Render a bound without a pointless '.0' on whole numbers."""
    return str(int(n)) if float(n).is_integer() else str(n)


def _num(resolved: dict[str, str], key: str) -> float | None:
    """Best-effort numeric read of one key from a {key: raw_value} map."""
    raw = resolved.get(key)
    if raw is None:
        spec = spec_by_key(key)
        raw = spec.default if spec is not None else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def check_invariants(resolved: dict[str, str]) -> None:
    """Cross-field rules that no single spec can express. `resolved` is the
    FULL {key: raw_value} map as it would be AFTER the pending change, so a
    save that breaks a pair is refused before it lands.

    Raises ValueError with an admin-readable message.
    """
    # 1. POST_COST_MIN ≤ POST_COST ≤ POST_COST_MAX
    lo = _num(resolved, "POST_COST_MIN")
    mid = _num(resolved, "POST_COST")
    hi = _num(resolved, "POST_COST_MAX")
    if None not in (lo, mid, hi):
        assert lo is not None and mid is not None and hi is not None  # for mypy
        if lo > hi:
            raise ValueError(
                f"POST_COST_MIN ({_fmt(lo)}) cannot exceed POST_COST_MAX ({_fmt(hi)})"
            )
        if not (lo <= mid <= hi):
            raise ValueError(
                f"POST_COST ({_fmt(mid)}) must sit between POST_COST_MIN "
                f"({_fmt(lo)}) and POST_COST_MAX ({_fmt(hi)})"
            )

    # 2. TIER_*_THRESHOLD strictly increasing — otherwise a band is unreachable
    prev_name, prev_val = None, None
    for tier in TIER_ORDER:
        val = _num(resolved, f"TIER_{tier}_THRESHOLD")
        if val is None:
            continue
        if prev_val is not None and val <= prev_val:
            raise ValueError(
                f"TIER_{tier}_THRESHOLD ({_fmt(val)}) must be greater than "
                f"TIER_{prev_name}_THRESHOLD ({_fmt(prev_val)}) — thresholds "
                "must strictly increase or the tier is unreachable"
            )
        prev_name, prev_val = tier, val

    # 3. TIER_*_MULTIPLIER ≥ 1 — a sub-1 multiplier silently taxes earners
    for tier in MULTIPLIER_TIERS:
        val = _num(resolved, f"TIER_{tier}_MULTIPLIER")
        if val is not None and val < 1:
            raise ValueError(
                f"TIER_{tier}_MULTIPLIER ({_fmt(val)}) must be ≥ 1 — a "
                "multiplier below 1 would shrink every earn for that tier"
            )
