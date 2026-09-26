/**
 * API client for Loudrr Mini App
 */

import { DESIGN_MODE, getMockResponse, MOCK_SHOULD_FAIL } from './mockData';

// Use local API proxy in production, direct backend in dev
const API_BASE_URL = typeof window !== 'undefined' && window.location.hostname !== 'localhost'
  ? '/api/miniapp'  // Use Next.js API routes (proxied to backend)
  : (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/miniapp');

// Loud API base URL
const LOUD_API_BASE_URL = typeof window !== 'undefined' && window.location.hostname !== 'localhost'
  ? '/api/loud'  // Use Next.js API routes (proxied to backend)
  : (process.env.NEXT_PUBLIC_API_URL?.replace('/api/miniapp', '/api/loud') || 'http://localhost:8000/api/loud');

// Debug telegram ID for local testing (only used when not in Telegram)
const DEBUG_TELEGRAM_ID = process.env.NEXT_PUBLIC_DEBUG_TELEGRAM_ID || '';

// Get Telegram Web App init data
function getTelegramInitData(): string {
  if (typeof window !== 'undefined' && (window as any).Telegram?.WebApp) {
    return (window as any).Telegram.WebApp.initData;
  }
  return '';
}

// Check if running inside Telegram
function isInTelegram(): boolean {
  return typeof window !== 'undefined' && !!(window as any).Telegram?.WebApp?.initData;
}

// FastAPI errors: {"detail": "text"}, or a 422's {"detail": [{msg, loc}, …]};
// the app's own errors use {"error": "text"}. Never "[object Object]".
function errorMessage(body: any, fallback: string): string {
  const detail = body?.detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (typeof d?.msg === 'string' ? d.msg : '')).filter(Boolean);
    if (msgs.length) return msgs.join('; ');
  }
  for (const v of [detail, body?.error, body?.message]) {
    if (typeof v === 'string' && v) return v;
  }
  return fallback || 'Request failed';
}

// API request helper
async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  // Design mode: serve mock data, never touch the backend.
  if (DESIGN_MODE) {
    const mock = getMockResponse(endpoint);
    await new Promise((r) => setTimeout(r, 150));
    // Sentinel: simulate a failing endpoint (e.g. "user does not exist yet").
    if (mock === MOCK_SHOULD_FAIL) {
      throw new Error('Not found (design mode)');
    }
    if (mock !== undefined) {
      return mock as T;
    }
    // No mock for this endpoint (e.g. a POST action) — return a benign success.
    return { success: true } as T;
  }

  const initData = getTelegramInitData();

  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(initData && { 'X-Telegram-Init-Data': initData }),
    ...options.headers,
  };

  // Add debug telegram_id for local testing when not in Telegram
  let url = `${API_BASE_URL}${endpoint}`;
  if (!isInTelegram() && DEBUG_TELEGRAM_ID) {
    const separator = endpoint.includes('?') ? '&' : '?';
    url = `${url}${separator}telegram_id=${DEBUG_TELEGRAM_ID}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    // FastAPI returns {"detail": "..."} on errors — check that first.
    // Also include the HTTP status code so future debugging isn't blind.
    const body = await response.json().catch(() => ({}));
    throw new Error(`${response.status}: ${errorMessage(body, response.statusText)}`);
  }

  return response.json();
}

// Loud API request helper (same auth, different base URL)
async function loudApiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  // Design mode: serve mock data, never touch the backend.
  if (DESIGN_MODE) {
    const mock = getMockResponse(endpoint);
    await new Promise((r) => setTimeout(r, 150));
    if (mock === MOCK_SHOULD_FAIL) {
      throw new Error('Not found (design mode)');
    }
    if (mock !== undefined) {
      return mock as T;
    }
    return { success: true } as T;
  }

  const initData = getTelegramInitData();

  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(initData && { 'X-Telegram-Init-Data': initData }),
    ...options.headers,
  };

  // Add debug telegram_id for local testing when not in Telegram
  let url = `${LOUD_API_BASE_URL}${endpoint}`;
  if (!isInTelegram() && DEBUG_TELEGRAM_ID) {
    const separator = endpoint.includes('?') ? '&' : '?';
    url = `${url}${separator}telegram_id=${DEBUG_TELEGRAM_ID}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    // FastAPI returns {"detail": "..."} on errors — check that first.
    // Also include the HTTP status code so future debugging isn't blind.
    const body = await response.json().catch(() => ({}));
    throw new Error(`${response.status}: ${errorMessage(body, response.statusText)}`);
  }

  return response.json();
}

// Types
export interface User {
  id: string;
  display_name: string;
  telegram_username: string;
  credits: number;
  daily_earned: number;
  daily_cap: number;
  total_engagements: number;
  tier: string;
  current_streak: number;
  // v1 fields
  is_pro?: boolean;
  x_username?: string;
  tweetscout_score?: number;
  tweetscout_last_updated?: string | null;  // ISO timestamp, null if never fetched
  // XProfile fields
  x_followers_count?: number;
  x_display_name?: string;
  // Honesty score (0-50)
  honesty_score?: number;
  // Engagement progress
  available_posts?: number;
  engaged_today?: number;
  // Whitelist status
  is_whitelisted?: boolean;
  // Feature access
  loud_access?: boolean;
  // X verification gate (post-approval)
  x_verified?: boolean;
  pending_claimed_x_username?: string | null;
  x_verification_pending_review?: boolean;
}

export interface Post {
  id: string;
  x_link: string;
  redirect_url: string;
  creator: string;
  creator_x_username?: string;
  creator_avatar?: string;  // X profile avatar URL
  escrow_remaining: number;
  engagement_progress: number;
  // v1 fields
  is_sponsored?: boolean;
  tweet_id?: string;
  created_at?: string;  // ISO date string
  // Cached tweet content for feed display
  tweet_text?: string;
  tweet_author_name?: string;
  tweet_author_username?: string;
  tweet_author_avatar?: string;
  tweet_media?: string[];
  tweet_created_at?: string;  // ISO date string
  hours_remaining?: number;   // Hours until post expires
  quick_reply?: string | null; // this viewer's Quick Reply draft, once written
}

export interface SessionResponse {
  posts: Post[];
  pending_count: number;           // User's unverified engagement count (persists across sessions)
  pending_post_ids: string[];      // Post IDs with pending engagements
  show_verification: boolean;      // True if user has 10+ pending engagements
  user?: {
    credits: number;
    daily_earned: number;
    daily_cap: number;
  };
  message?: string;
  // Legacy fields (kept for backward compatibility)
  session_token?: string | null;
  clicked_posts?: string[];
  expires_at?: string;
  resumed?: boolean;
}

export interface ClickResponse {
  success: boolean;
  engagement_id: string;
  created: boolean;               // True if new engagement created
  pending_count: number;          // Updated pending count
  show_verification: boolean;     // True if user now has 10+ pending
}

export interface CompleteResponse {
  success: boolean;
  message: string;
  credits_awarded: number;
  new_balance?: number;
  daily_earned?: number;
  pending_count?: number;          // Remaining pending (failed verifications)
  pending_post_ids?: string[];     // IDs of posts still pending verification
  verification_results?: Array<{
    post_id: string;
    passed: boolean;
  }>;
  // New simplified response fields
  passed?: number;                 // Number of passed verifications
  failed?: number;                 // Number of failed verifications (need re-engagement)
  total_verified?: number;         // Total verified (same as passed)
  honesty_score?: number;          // User's honesty score (0-50)
  // Legacy fields (for backwards compatibility)
  passes?: number;
  verification_ratio?: number;
  penalty_applied?: number;
  warning?: boolean;
  retry_required?: boolean;
  failures?: number;
}

export interface SubmitPostResponse {
  success: boolean;
  message: string;
  post_id?: string;
  new_balance?: number;
  escrow?: number;
  error?: string;
}

export interface AppSettings {
  post_cost_min: number;
  post_cost_max: number;
  // Claim threshold (MIN_ENGAGEMENTS_TO_CLAIM) — admin-tunable, mirrored in the
  // Engage tab so the button never disagrees with the backend gate.
  min_engagements_to_claim?: number;
  // Live tier bands (highest threshold first) — admins can retune these at
  // runtime, so surfaces that label a score should read them from here rather
  // than hardcoding a copy. See app/waitlist/[username]/page.tsx.
  tiers?: { name: string; min_score: number; multiplier?: number }[];
}

export interface QueueClaimResponse {
  success: boolean;
  batch_id?: string;
  status?: string;
  position?: number;
  engagement_count?: number;
  message: string;
  pending_count?: number;
  remaining_seconds?: number;
  error?: string;
}

export interface ClaimBatch {
  id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  engagement_count: number;
  passed: number | null;
  failed: number | null;
  credits_awarded: number | null;
  message: string;
  created_at: string;
  completed_at: string | null;
}

export interface ClaimHistoryResponse {
  batches: ClaimBatch[];
  pending_engagements: number;
  has_processing: boolean;
}

export interface UserStats {
  user: {
    display_name: string;
    telegram_username: string;
    credits: number;
    tier: string;
    current_streak: number;
    total_credits_earned: number;
    total_credits_spent: number;
  };
  posts: {
    total: number;
    active: number;
    completed: number;
  };
  engagements: {
    given: number;
    received: number;
  };
  recent_posts: Array<{
    id: string;
    x_link: string;
    status: string;
    escrow_remaining: number;
    engagement_progress: number;
    created_at: string;
  }>;
}

// Waitlist registration types
export interface OtherPlatformEntry {
  platform: 'youtube' | 'tiktok' | 'other';
  username: string;
  platform_name?: string;
}

// Waitlist card enrichment (miniapp waitlist-pending screen)
export interface WaitlistEnrichment {
  // The X handle the backend resolved for the caller. Frontend compares this
  // to the handle it's about to render — enrichment is only overlaid when
  // they match, so caller A viewing /waitlist/registered?u=someone_else can't
  // paint their own score onto someone else's card.
  x_username: string | null;
  score: number | null;
  tier: string | null;
  followers: string[];
  followers_count: number;         // total smart followers (>= followers.length)
  // 'pending' = the sign-up score fetch hasn't landed yet (poll briefly);
  // 'not_found' = fetched, but there's no score for this account yet
  score_status: 'pending' | 'ready' | 'not_found';
  score_updated_at: string | null;
}

// POST /user/refresh-score/ — the card fields plus what happened
export interface ScoreRefreshResult extends WaitlistEnrichment {
  result: 'updated' | 'not_found' | 'cooldown' | 'unavailable';
  retry_after_seconds: number;     // > 0 with 'cooldown'
}

// API Functions
export const api = {
  /**
   * Get app settings (post cost min/max, etc.)
   * Cached on backend, fetched once on frontend load
   */
  getSettings: () => apiRequest<AppSettings>('/settings/'),

  /**
   * Get current user info
   */
  getUser: () => apiRequest<User>('/user/'),

  /**
   * Start X OAuth verification — returns the X authorize URL the user
   * should be sent to (open in external browser via Telegram WebApp.openLink).
   */
  startXOAuth: () => apiRequest<{ authorize_url: string }>('/x-oauth/start/', { method: 'POST' }),

  /**
   * After OAuth returned a different X username than the one the user
   * submitted, the user clicks "yes this IS my actual account" — this
   * creates an XVerificationRequest for admin review.
   */
  confirmXMismatch: () =>
    apiRequest<{ status: string }>('/x-verification/confirm-mismatch/', { method: 'POST' }),

  /**
   * "no, that wasn't my real account" — clears pending state so the user
   * can retry Connect X with the correct account.
   */
  cancelXMismatch: () =>
    apiRequest<{ status: string }>('/x-verification/cancel-mismatch/', { method: 'POST' }),

  /**
   * Start engagement flow - returns posts and user's pending progress
   * Progress persists indefinitely (no session expiry)
   */
  startSession: () => apiRequest<SessionResponse>('/session/start/', { method: 'POST' }),

  /**
   * Record a click/engagement on a post
   * Creates Engagement with verified=False (pending verification)
   * No session token needed - tracks at user level
   */
  recordClick: (postId: string) =>
    apiRequest<ClickResponse>('/session/click/', {
      method: 'POST',
      body: JSON.stringify({ post_id: postId }),
    }),

  /**
   * Verify user returned after clicking a link (optional)
   */
  verifyReturn: (postId: string) =>
    apiRequest<{ success: boolean; verified: boolean; engagement_id?: string }>('/session/verify-return/', {
      method: 'POST',
      body: JSON.stringify({ post_id: postId }),
    }),

  /**
   * Submit a new X post
   * @param xLink - The X/Twitter post URL
   * @param karmaAmount - Amount of karma to spend (between min and max from settings)
   */
  submitPost: (xLink: string, karmaAmount: number) =>
    apiRequest<SubmitPostResponse>('/post/submit/', {
      method: 'POST',
      body: JSON.stringify({ x_link: xLink, karma_amount: karmaAmount }),
    }),

  /**
   * Get detailed user stats
   */
  getUserStats: () => apiRequest<UserStats>('/user/stats/'),

  /**
   * Link X/Twitter account
   * Returns XProfile data including TweetScout score and tier
   */
  linkXAccount: (xUsername: string) =>
    apiRequest<{
      success: boolean;
      x_username: string;
      tweetscout_score: number;
      tier: string;
      followers_count: number;
      display_name: string;
    }>(
      '/user/link-x/',
      {
        method: 'POST',
        body: JSON.stringify({ x_username: xUsername }),
      }
    ),

  /**
   * Queue verification for async processing (instant response)
   * Like spot trading - queues and returns immediately
   */
  queueClaim: () =>
    apiRequest<QueueClaimResponse>('/session/queue-claim/', {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  /**
   * Get claim/verification history
   * Returns recent batches with status and results
   */
  getClaimHistory: () =>
    apiRequest<ClaimHistoryResponse>('/claims/history/'),

  /**
   * Complete onboarding - fetches TweetScout and activates user
   * Called when user clicks "Let's Go Loudrr" button
   */
  completeOnboarding: () =>
    apiRequest<{
      success: boolean;
      tweetscout_score: number;
      tier: string;
      followers_count?: number;
      display_name?: string;
      already_onboarded?: boolean;
      message?: string;
    }>('/onboarding/complete/', {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  /**
   * Register interest in a coming feature
   */
  registerFeatureInterest: (feature: string, interests: string[]) =>
    apiRequest<{ success: boolean }>('/feature-interest/', {
      method: 'POST',
      body: JSON.stringify({ feature, interests }),
    }),

  /**
   * Check if user has registered interest in a feature
   */
  getFeatureInterest: (feature: string) =>
    apiRequest<{ registered: boolean }>(`/feature-interest/?feature=${feature}`),

  // =============================================================================
  // WAITLIST - In-App Registration
  // =============================================================================

  /**
   * Check waitlist status for current Telegram user
   * Returns: "approved" | "waitlisted" | "not_registered"
   */
  checkWaitlistStatus: () =>
    apiRequest<{
      status: 'approved' | 'waitlisted' | 'rejected' | 'not_registered';
      x_username?: string;
      submitted_at?: string;
      referral_code?: string;
      reason?: string;  // rejected only
    }>('/waitlist/status/'),

  /**
   * Best-effort enrichment for the miniapp waitlist-pending card. Returns
   * the authed user's own score + top-10 smart followers (derived server-side
   * from their linked X handle). Backend never 500s — empty shape when
   * analytics is down or no X handle is on file.
   */
  getWaitlistEnrichment: () =>
    apiRequest<WaitlistEnrichment>('/user/waitlist-enrichment/'),

  /**
   * Re-fetch the caller's score now (applicants and approved users). Scores
   * are only ever fetched at sign-up and by this call — there's no scheduler —
   * and the backend allows one refresh per account per cooldown window.
   */
  refreshScore: () =>
    apiRequest<ScoreRefreshResult>('/user/refresh-score/', { method: 'POST' }),

  /**
   * Kick off the waitlist-specific X OAuth flow. The backend creates a
   * pre-signup state row (keyed on the caller's telegram_id, not a User) and
   * returns the X authorize URL. Frontend opens it via Telegram WebApp
   * openLink; the callback lands at /waitlist/oauth-return with a signed
   * proof that we echo back on registerWaitlist().
   */
  startWaitlistXOAuth: () =>
    apiRequest<{ authorize_url: string }>('/waitlist/x-oauth/start/', { method: 'POST' }),

  /**
   * Poll for the outcome of the X OAuth attempt (Telegram WebView flow). The
   * OAuth chain completes in the external system browser, whose storage the
   * mini-app WebView can't see — so the backend stores the outcome keyed by
   * telegram_id. All null while X is still open; then either the proof (with
   * the verified handle — never decode the token client-side, it's
   * compressed) or an error code (reported once). The proof stays readable
   * until registration.
   */
  pollWaitlistXOAuthProof: () =>
    apiRequest<{
      proof: string | null;
      x_username: string | null;
      expires_in: number | null;   // seconds left on the proof
      error: 'denied' | 'invalid' | 'expired' | 'token' | 'profile' | 'cancelled' | null;
      // true once someone authorized on X but nobody has confirmed the link in
      // the browser yet — the handle stays hidden until they do
      awaiting_confirmation?: boolean;
    }>('/waitlist/x-oauth/proof/'),

  /**
   * What the browser that just authorized on X is about to link. Public: this
   * tab has no Telegram session, so the one-time token from the callback is the
   * only credential — and it travels in the BODY, so it never reaches a request
   * log, a Referer header or browser history.
   */
  getWaitlistXOAuthConfirmation: (token: string) =>
    apiRequest<{ x_username: string; telegram_label: string; expires_in: number }>(
      '/waitlist/x-oauth/confirm/info/',
      { method: 'POST', body: JSON.stringify({ token }) },
    ),

  /** The answer. `confirm` releases the proof to that Telegram account's mini-app. */
  decideWaitlistXOAuthConfirmation: (token: string, decision: 'confirm' | 'cancel') =>
    apiRequest<{ ok: boolean; status: 'confirmed' | 'cancelled' }>(
      '/waitlist/x-oauth/confirm/',
      { method: 'POST', body: JSON.stringify({ token, decision }) },
    ),

  /**
   * Register for waitlist directly from the mini-app. Telegram-only signup —
   * telegram_id comes from the signed initData header set by the client
   * middleware, so it's NOT in the request body. The x_proof field is the
   * signed itsdangerous token minted by /api/auth/x/callback/waitlist/ — the
   * server re-verifies it and uses its embedded (verified) X handle. Sends
   * the waitlist card via Telegram on success.
   */
  registerWaitlist: (body: {
    x_proof: string;
    referral_code?: string;
    region?: string;
    niche?: string;
    other_platforms?: OtherPlatformEntry[];
  }) =>
    apiRequest<{
      status: 'registered' | 'already_registered';
      message: string;
      x_username: string;   // the entry's verified handle
      referral_code?: string;
    }>('/waitlist/register/', {
      method: 'POST',
      body: JSON.stringify({
        x_proof: body.x_proof,
        ...(body.referral_code && { referral_code: body.referral_code }),
        ...(body.region && { region: body.region }),
        ...(body.niche && { niche: body.niche }),
        ...(body.other_platforms?.length && { other_platforms: body.other_platforms }),
      }),
    }),
};

// =============================================================================
// LOUD API - UGC Rewards Feature
// =============================================================================

export interface LoudProject {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  description: string;
  ends_at: string;
  time_remaining_hours: number;
  reward_pool: string;
  min_tweetscout_score: number;
  max_submissions: number;
  user_submissions: number;
  can_submit: boolean;
  cannot_submit_reason: string | null;
  total_participants: number;
  your_rank: number | null;
  your_points: number;
}

export interface LoudProjectsResponse {
  projects: LoudProject[];
  daily_submissions_remaining: number;
  daily_limit: number;
  expected_points: number;
  user_tweetscout_score: number;
}

export interface LoudSubmitResponse {
  success: boolean;
  submission_id?: string;
  points_awarded?: number;
  new_total_points?: number;
  new_rank?: number;
  daily_submissions_remaining?: number;
  project_submissions_remaining?: number;
  error?: string;
}

export interface LoudLeaderboardUser {
  id: string;
  display_name: string;
  x_username: string | null;
  avatar: string | null;
}

export interface LoudLeaderboardEntry {
  rank: number;
  user: LoudLeaderboardUser;
  total_points: number;
  submission_count: number;
}

export interface LoudUserEntry {
  user_id?: string;
  rank: number | null;
  total_points: number;
  submission_count: number;
}

export interface LoudLeaderboardResponse {
  project: {
    name: string;
    slug: string;
    ends_at: string;
    reward_pool: string;
  };
  leaderboard: LoudLeaderboardEntry[];
  user_entry: LoudUserEntry | null;
  total_participants: number;
}

// Loud API Functions
export const loudApi = {
  /**
   * Get live projects with user's submission counts and eligibility
   */
  getProjects: () => loudApiRequest<LoudProjectsResponse>('/projects/'),

  /**
   * Submit content to a project
   * @param projectId - The project UUID
   * @param xLink - The X/Twitter post URL
   */
  submit: (projectId: string, xLink: string) =>
    loudApiRequest<LoudSubmitResponse>('/submit/', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, x_link: xLink }),
    }),

  /**
   * Get project leaderboard
   * @param projectSlug - The project slug
   */
  getLeaderboard: (projectSlug: string) =>
    loudApiRequest<LoudLeaderboardResponse>(`/leaderboard/${projectSlug}/`),
};

// =============================================================================
// ADMIN API - RBAC-gated operations (require user.role = admin or superadmin)
// =============================================================================

// Admin endpoints are at backend /api/admin/*; the proxy in next.config.ts
// preserves the prefix (unlike miniapp/loud which strip it) so the URL is
// the same on both sides.
const ADMIN_API_BASE_URL = typeof window !== 'undefined' && window.location.hostname !== 'localhost'
  ? '/api/admin'
  : (process.env.NEXT_PUBLIC_ADMIN_API_URL || 'http://localhost:8000/api/admin');

async function adminApiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  // The admin panel is a website: identity is the session cookie from the
  // Telegram Login Widget (sent automatically, same origin). The custom header
  // is the backend's CSRF check — a cross-site form can't set it.
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    'X-Requested-With': 'loudrr-admin',
    ...options.headers,
  };

  let url = `${ADMIN_API_BASE_URL}${endpoint}`;
  if (!isInTelegram() && DEBUG_TELEGRAM_ID) {
    const separator = endpoint.includes('?') ? '&' : '?';
    url = `${url}${separator}telegram_id=${DEBUG_TELEGRAM_ID}`;
  }

  const response = await fetch(url, { ...options, headers });

  if (!response.ok) {
    // Surface FastAPI's {"detail": ...} shape (422 arrays included), with status code
    const body = await response.json().catch(() => ({}));
    throw new Error(`${response.status}: ${errorMessage(body, response.statusText)}`);
  }
  return response.json();
}

export interface PendingWaitlistEntry {
  id: string;
  telegram_id: number | null;
  telegram_username: string;
  x_username: string;
  // True for OAuth-first registrations (handle verified against X's /users/me);
  // false only for legacy paste-a-link entries.
  x_verified: boolean;
  region: string;
  niche: string;
  created_at: string | null;
  // stored at sign-up (or the applicant's last refresh); score null +
  // score_updated_at null = not fetched yet
  score: number | null;
  tier: string | null;
  smart_followers: number | null;
  score_updated_at: string | null;
}

export interface PendingXVerification {
  id: string;
  user_id: string;
  user_telegram_username: string;
  submitted_x_username: string;
  claimed_x_username: string;
  created_at: string | null;
}

// ---------------------------------------------------------------------------
// The two review queues (GET /api/admin/waitlist/ and /x-verification/).
//
// These replace the old `/pending/` reads, which returned a bare array of the
// first 200 `submitted` rows: no total, no paging, no history, and a third of
// the columns the reviewer actually decides on left in the database.
//
// Every timestamp below is a NAIVE UTC ISO string with no `Z` — render it
// through lib/admin/format.ts, never `new Date(iso)`.
// ---------------------------------------------------------------------------

/** `{items, total, limit, offset}` — `total` counts every match, pre-paging. */
export interface AdminPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type WaitlistReviewStatus = 'submitted' | 'approved' | 'rejected';

export interface WaitlistReviewRow {
  id: string;
  status: WaitlistReviewStatus;

  // identity — telegram_username is "" for a good number of rows; fall back to
  // telegram_display_name, then the numeric id, before rendering a dash
  telegram_id: number | null;
  telegram_username: string;
  telegram_display_name: string;
  x_username: string;
  x_user_id: string;
  x_link: string;
  x_verified: boolean;
  x_verified_previously: boolean;

  // signal. score === null && score_updated_at === null  -> never fetched
  //         score === null && score_updated_at !== null  -> provider had none
  score: number | null;
  tier: string | null;
  score_updated_at: string | null;
  followers_count: number | null;
  following_count: number | null;
  tweets_count: number | null;
  smart_followers: number | null;
  register_date: string | null;   // "2012-07-25" — account age
  bio: string;
  profile_name: string;
  avatar: string;
  x_blue_verified: boolean;
  category: string;

  // profile
  region: string;                 // raw enum value, e.g. "cis_eastern_europe"
  niche: string;                  // raw enum value, e.g. "ai_tech"
  other_platforms: Array<{ platform?: string; username?: string; platform_name?: string | null }>;

  // referral
  referral_code: string;
  referral_code_used: string;
  referrer_handle: string;
  /** Total applications that came in on `referral_code_used` — a ring tell. */
  referral_code_uses: number;
  total_referrals: number;

  // decision bookkeeping (populated on the approved / rejected tabs)
  rejection_reason: string;
  decided_at: string | null;
  decided_by_handle: string;
  created_user_id: string | null;
  created_at: string | null;
}

export interface WaitlistQuery {
  q?: string;
  status?: WaitlistReviewStatus | '';
  sort?: 'created' | 'score' | 'tier';
  dir?: 'asc' | 'desc';
  region?: string;
  niche?: string;
  hasScore?: boolean | null;
  limit?: number;
  offset?: number;
}

export interface WaitlistFacets {
  regions: string[];
  niches: string[];
  statuses: WaitlistReviewStatus[];
}

export interface RefreshScoreResult {
  ok: boolean;
  /** true = handed to arq, the row updates in the background. */
  queued: boolean;
  /** Inline outcome when there's no queue: found | not_found | unavailable | skipped. */
  result: 'found' | 'not_found' | 'unavailable' | 'skipped' | null;
  entry: WaitlistReviewRow;
}

export type XVerificationReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface XVerificationPriorRequest {
  id: string;
  submitted_x_username: string;
  claimed_x_username: string;
  claimed_x_user_id: string;
  status: XVerificationReviewStatus;
  /** Reviewer-to-reviewer note. Never sent to the user. */
  admin_notes: string;
  created_at: string | null;
  reviewed_at: string | null;
}

export interface XVerificationReviewRow extends XVerificationPriorRequest {
  user_id: string;
  user_telegram_id: number | null;
  user_telegram_username: string;
  user_display_name: string;
  /** The handle the account holds today — not necessarily either of the two. */
  user_x_username: string;
  user_score: number | null;
  user_tier: string | null;
  user_credits: number;
  user_is_banned: boolean;
  user_x_verified: boolean;
  user_created_at: string | null;
  /** The same user's other requests, newest first. */
  prior_requests: XVerificationPriorRequest[];
  /** Non-null => approve will 409. Disable Approve and say why. */
  claimed_handle_taken_by: {
    user_id: string;
    telegram_username: string;
    x_username: string;
    is_banned: boolean;
  } | null;
}

export interface XVerificationQuery {
  q?: string;
  status?: XVerificationReviewStatus | '';
  limit?: number;
  offset?: number;
}

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    // "" and null mean "no filter" — don't send them, so the backend's own
    // defaults (status=submitted, sort=created) stay in one place
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

export interface AdminUserRow {
  id: string;
  telegram_id: number | null;
  telegram_username: string;
  x_username: string;
  display_name: string;              // identity fallback when there is no handle
  credits: number;
  role: '' | 'admin' | 'superadmin';
  is_banned: boolean;
  is_whitelisted: boolean;
  x_verified: boolean;
  total_engagements: number;
  created_at: string | null;         // NAIVE UTC — render through lib/admin/format
  tweetscout_score: number;          // score from the score provider (0 if never scored)
  tier: string;                      // tier that score maps to (karma multiplier)
  score_updated_at: string | null;   // last refresh attempt, ISO; null = never scored
}

/** Sort keys the users endpoint accepts. Anything else is a 422. */
export type AdminUserSort = 'created_at' | 'credits' | 'tweetscout_score' | 'total_engagements';
/** Server-side row filters. '' means no filter. */
export type AdminUserFlag = '' | 'banned' | 'not_whitelisted' | 'admins' | 'never_scored';

export interface AdminUserQuery {
  q?: string;
  flag?: AdminUserFlag;
  sort?: AdminUserSort;
  dir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

/**
 * `{rows, total}` — the envelope the users endpoints answer with. (The review
 * queues above use `AdminPage`/`items`; these are the user-side lists.)
 */
export interface AdminRowsPage<T> {
  rows: T[];
  total: number;
}

export interface AdminUsersPage extends AdminRowsPage<AdminUserRow> {
  limit: number;
  offset: number;
}

export interface AdminUserDetail {
  id: string;
  telegram_id: number | null;
  telegram_username: string;
  x_username: string;
  display_name: string;
  referral_code: string;
  created_at: string | null;
  is_platform_account: boolean;
  flags: {
    role: '' | 'admin' | 'superadmin';
    is_banned: boolean;
    is_whitelisted: boolean;
    x_verified: boolean;
    x_verified_at: string | null;
    loud_access: boolean;
    pending_x_verification: boolean;
    pending_claimed_x_username: string;
  };
  score: {
    tweetscout_score: number;
    tier: string;
    multiplier: number;
    score_updated_at: string | null;
  };
  credits: {
    balance: number;
    total_earned: number;
    total_spent: number;
    /** min(balance, earned - spent) — karma above this cannot actually be spent. */
    spendable_headroom: number;
    daily_earned: number;
    daily_reset_at: string | null;
    escrow_locked: number;
  };
  xp: {
    sponsored_xp: number;
    total_sponsored_xp_earned: number;
    sponsored_engagements: number;
  };
  activity: {
    total_engagements: number;
    total_posts: number;
    engagements_recorded: number;
    engagements_pending: number;
    posts_active: number;
    posts_completed: number;
    posts_cancelled: number;
    current_streak: number;
    longest_streak: number;
    last_engagement_date: string | null;
    honesty_score: number;
  };
  waitlist: {
    id: string;
    status: string;
    x_username: string;
    x_verified: boolean;
    region: string;
    niche: string;
    score: number | null;
    created_at: string | null;
    approved_at: string | null;
    rejection_reason: string;
  } | null;
  referrals: {
    code: string;
    referred_by_code: string;
    referrals_made: number;
  };
}

export interface AdminUserTransaction {
  id: string;
  type: 'earned' | 'spent' | 'refund' | 'admin_grant' | 'apply_penalty';
  amount: number;
  balance_after: number;
  description: string;
  reference_id: string | null;
  reference_type: string;
  created_at: string | null;
}

export interface AdminUserPost {
  id: string;
  x_link: string;
  tweet_text: string;
  status: string;
  is_sponsored: boolean;
  escrow: number;
  initial_escrow: number;
  engagements: number;
  created_at: string | null;
  completed_at: string | null;
}

export interface AdminUserEngagement {
  id: string;
  post_id: string;
  post_link: string;
  post_author: string;
  is_sponsored: boolean;
  verified: boolean;
  credit_granted: boolean;
  like_verified: boolean;
  reply_verified: boolean;
  clicked_at: string | null;
}

export interface AdminUserAuditRow {
  id: string;
  action: string;
  detail: Record<string, unknown>;
  actor_id: string | null;
  actor_handle: string;
  actor_role: string;
  created_at: string | null;
}

export interface SiteSettingRow {
  key: string;
  value: string;
  default: string;
  data_type: 'int' | 'float' | 'decimal' | 'bool' | 'str';
  description: string;
  live: boolean;       // true if backend code currently reads this
  persisted: boolean;  // false means: no SiteSetting row yet, default shown
  // Bounds the SERVER enforces; mirrored onto the input so the two can't drift.
  // null on non-numeric settings.
  min: number | null;
  max: number | null;
  step: number | null;
  unit: string;        // short suffix shown beside the field ('karma', 'seconds')
  danger: boolean;     // money/availability critical → confirm before saving
  impact: string;      // one-line "what this actually does" for that confirm
  drifted: boolean;    // current value differs from the shipped default
}

export interface SiteSettingsGroup {
  name: string;
  description: string;
  settings: SiteSettingRow[];
}

export interface SiteSettingsResponse {
  groups: SiteSettingsGroup[];
  /** How long another PROCESS (the arq settlement worker) may keep a stale copy. */
  propagation_seconds: number;
}

export interface SiteSettingHistoryRow {
  id: string;
  old_value: string | null;
  new_value: string | null;
  actor_id: string | null;
  actor_handle: string;
  created_at: string | null;
}

export interface AdminStats {
  users: {
    total: number;
    by_role: { regular: number; admin: number; superadmin: number };
    banned: number;
    whitelisted: number;
    x_verified: number;
    new_this_week: number;
  };
  credits: {
    in_circulation: number;
    total_earned: number;
    total_spent: number;
  };
  posts: {
    active: number;
    completed: number;
    cancelled: number;
    total_escrow_active: number;
  };
  engagements: {
    total: number;
    today: number;
    this_week: number;
  };
  queues: {
    pending_waitlist: number;
    pending_x_verifications: number;
    pending_batches: number;
  };
  recent_audit: Array<{
    id: string;
    actor_id: string | null;
    action: string;
    target_type: string;
    target_id: string | null;
    detail: Record<string, unknown>;
    created_at_iso: string;
  }>;
}

export type TimeseriesMetric = 'karma_earned' | 'engagements' | 'new_users';
export interface TimeseriesPoint { date: string; value: number; }
export interface TimeseriesResponse {
  metric: TimeseriesMetric;
  days: number;
  points: TimeseriesPoint[];
  total: number;
  delta_pct: number | null;
}

// An X account whose new original posts (not replies/retweets) become
// sponsored raid posts. Timestamps are naive UTC ISO strings (no zone suffix).
export interface SponsorRow {
  id: string;
  x_username: string;           // lowercase, no "@"
  x_user_id: string;
  display_name: string;
  avatar_url: string;
  is_active: boolean;
  karma_per_post: number;       // escrow each new sponsored post is funded with
  notes: string;
  posts_created: number;        // sponsored posts ever created from this account
  active_posts: number;         // of those, still live in Engage
  posts_total: number;
  karma_paid: number;           // karma engagers have earned from its posts
  engagements: number;
  last_post_at: string | null;
  last_tweet_id: string | null;
  active_since: string | null;
  created_at: string | null;
}

export type SponsorPatch = Partial<Pick<SponsorRow, 'is_active' | 'karma_per_post' | 'notes'>>;

export const adminApi = {
  // ---- read ----
  pendingWaitlist: (limit = 50) =>
    adminApiRequest<PendingWaitlistEntry[]>(`/waitlist/pending/?limit=${limit}`),
  pendingXVerifications: (limit = 50) =>
    adminApiRequest<PendingXVerification[]>(`/x-verification/pending/?limit=${limit}`),
  /**
   * Paged + filtered user list. `q` matches telegram/x handle, display name,
   * referral code, telegram id and uuid prefix (server-side, LIKE-escaped).
   */
  listUsers: (query: AdminUserQuery = {}) => {
    const p = new URLSearchParams();
    if (query.q) p.set('q', query.q);
    if (query.flag) p.set('flag', query.flag);
    p.set('sort', query.sort ?? 'created_at');
    p.set('dir', query.dir ?? 'desc');
    p.set('limit', String(query.limit ?? 25));
    p.set('offset', String(query.offset ?? 0));
    return adminApiRequest<AdminUsersPage>(`/users/?${p.toString()}`);
  },
  /** Rows only — for callers (the dashboard tier donut) that just want a sample. */
  searchUsers: (q = '', limit = 50) =>
    adminApiRequest<AdminUsersPage>(
      `/users/?q=${encodeURIComponent(q)}&limit=${limit}`
    ).then((page) => page.rows),

  // ---- user detail (drill-down) ----
  getUser: (userId: string) => adminApiRequest<AdminUserDetail>(`/users/${userId}/`),
  getUserTransactions: (userId: string, limit = 25, offset = 0) =>
    adminApiRequest<AdminRowsPage<AdminUserTransaction>>(
      `/users/${userId}/transactions/?limit=${limit}&offset=${offset}`
    ),
  getUserPosts: (userId: string, limit = 25, offset = 0) =>
    adminApiRequest<AdminRowsPage<AdminUserPost>>(
      `/users/${userId}/posts/?limit=${limit}&offset=${offset}`
    ),
  getUserEngagements: (userId: string, limit = 25, offset = 0) =>
    adminApiRequest<AdminRowsPage<AdminUserEngagement>>(
      `/users/${userId}/engagements/?limit=${limit}&offset=${offset}`
    ),
  getUserAudit: (userId: string, limit = 25, offset = 0) =>
    adminApiRequest<AdminRowsPage<AdminUserAuditRow>>(
      `/users/${userId}/audit/?limit=${limit}&offset=${offset}`
    ),

  // ---- user ops ----
  /**
   * `granted` is what actually landed — 0 with `duplicate: true` when the same
   * `requestId` was already used (double-click / retried fetch).
   */
  grantCredits: (userId: string, amount: number, description = '', requestId = '') =>
    adminApiRequest<{
      ok: boolean; user_id: string; credits: number;
      requested: number; granted: number; duplicate: boolean;
    }>(
      `/users/${userId}/grant-credits/`,
      {
        method: 'POST',
        body: JSON.stringify({
          amount: String(amount), description, request_id: requestId,
        }),
      }
    ),
  /**
   * `deducted` is what actually left the balance — apply_penalty clamps to the
   * balance, so a 400 revoke against 355.35 removes 355.35 (`clamped: true`).
   * Toast THAT number, never the requested one.
   */
  revokeCredits: (userId: string, amount: number, reason = '', requestId = '') =>
    adminApiRequest<{
      ok: boolean; user_id: string; credits: number;
      requested: number; deducted: number; clamped: boolean; duplicate: boolean;
    }>(
      `/users/${userId}/revoke-credits/`,
      {
        method: 'POST',
        body: JSON.stringify({ amount: String(amount), reason, request_id: requestId }),
      }
    ),
  banUser: (userId: string, reason = '') =>
    adminApiRequest<{
      ok: boolean; user_id: string; is_banned: boolean; is_whitelisted: boolean;
    }>(
      `/users/${userId}/ban/`,
      { method: 'POST', body: JSON.stringify({ reason }) }
    ),
  /** Also restores the whitelist flag the ban cleared, when one was recorded. */
  unbanUser: (userId: string) =>
    adminApiRequest<{
      ok: boolean; user_id: string; is_banned: boolean; is_whitelisted: boolean;
    }>(
      `/users/${userId}/unban/`,
      { method: 'POST', body: JSON.stringify({}) }
    ),
  setWhitelist: (userId: string, value: boolean) =>
    adminApiRequest<{ ok: boolean; user_id: string; is_whitelisted: boolean }>(
      `/users/${userId}/whitelist/`,
      { method: 'POST', body: JSON.stringify({ value }) }
    ),

  // ---- waitlist review queue ----
  listWaitlist: (query: WaitlistQuery = {}) =>
    adminApiRequest<AdminPage<WaitlistReviewRow>>(
      `/waitlist/${queryString({
        q: query.q,
        status: query.status,
        sort: query.sort,
        dir: query.dir,
        region: query.region,
        niche: query.niche,
        has_score: query.hasScore ?? null,
        limit: query.limit,
        offset: query.offset,
      })}`
    ),
  waitlistFacets: () => adminApiRequest<WaitlistFacets>(`/waitlist/facets/`),

  // ---- waitlist ops ----
  approveWaitlist: (entryId: string) =>
    adminApiRequest<{ ok: boolean; created_user_id: string }>(
      `/waitlist/${entryId}/approve/`,
      { method: 'POST', body: JSON.stringify({}) }
    ),
  /**
   * `reason` is DM'd to the applicant verbatim; `internalNote` only ever
   * reaches audit_logs. They are NOT interchangeable — the single field this
   * replaced was labelled "internal" in the UI and mailed out anyway.
   */
  rejectWaitlist: (entryId: string, reason = '', internalNote = '') =>
    adminApiRequest<{ ok: boolean; entry_id: string; status: string }>(
      `/waitlist/${entryId}/reject/`,
      { method: 'POST', body: JSON.stringify({ reason, internal_note: internalNote }) }
    ),
  /** Undo a rejection. 409 unless the entry is `rejected` and userless. */
  reopenWaitlist: (entryId: string, internalNote = '') =>
    adminApiRequest<{ ok: boolean; entry_id: string; status: string }>(
      `/waitlist/${entryId}/reopen/`,
      { method: 'POST', body: JSON.stringify({ internal_note: internalNote }) }
    ),
  /** Re-run the score fetch for one applicant (queued, or inline in dev). */
  refreshWaitlistScore: (entryId: string) =>
    adminApiRequest<RefreshScoreResult>(
      `/waitlist/${entryId}/refresh-score/`,
      { method: 'POST', body: JSON.stringify({}) }
    ),

  // ---- x-verification review queue ----
  listXVerifications: (query: XVerificationQuery = {}) =>
    adminApiRequest<AdminPage<XVerificationReviewRow>>(
      `/x-verification/${queryString({
        q: query.q,
        status: query.status,
        limit: query.limit,
        offset: query.offset,
      })}`
    ),

  // ---- x-verification ops ----
  approveXVerification: (requestId: string) =>
    adminApiRequest<{ ok: boolean; request_id: string; status: string }>(
      `/x-verification/${requestId}/approve/`,
      { method: 'POST', body: JSON.stringify({}) }
    ),
  /** Same split as rejectWaitlist: `reason` is sent, `internalNote` is not. */
  rejectXVerification: (requestId: string, reason = '', internalNote = '') =>
    adminApiRequest<{ ok: boolean; request_id: string; status: string }>(
      `/x-verification/${requestId}/reject/`,
      { method: 'POST', body: JSON.stringify({ reason, internal_note: internalNote }) }
    ),

  me: () =>
    adminApiRequest<{
      id: string;
      telegram_id: number | null;
      telegram_username: string;
      role: 'admin' | 'superadmin' | '';
    }>(`/me/`),

  // ---- website sign-in (Telegram Login Widget) ----
  authConfig: () => adminApiRequest<{ bot_username: string }>(`/auth/config/`),
  telegramLogin: (payload: Record<string, unknown>) =>
    adminApiRequest<{ ok: boolean; role: string; telegram_username: string }>(
      `/auth/telegram/`, { method: 'POST', body: JSON.stringify(payload) }),
  logout: () => adminApiRequest<{ ok: boolean }>(`/auth/logout/`, { method: 'POST', body: '{}' }),

  getStats: () => adminApiRequest<AdminStats>(`/stats/`),

  /**
   * Exact moderation backlog counts for the sidebar badges.
   *
   * Reads the `queues` block of the existing GET /api/admin/stats/ (single
   * COUNT per queue) instead of measuring `pendingWaitlist(limit).length`,
   * which saturates at the list limit — a 200-deep waitlist used to render
   * as "50".
   */
  queues: () => adminApiRequest<AdminStats>(`/stats/`).then((s) => s.queues),

  getTimeseries: (metric: TimeseriesMetric, days = 30) =>
    adminApiRequest<TimeseriesResponse>(`/stats/timeseries?metric=${metric}&days=${days}`),

  getSiteSettings: () => adminApiRequest<SiteSettingsResponse>(`/site-settings/`),

  // trailing slash on purpose: the slashless form 307-redirects the browser to
  // the backend's INTERNAL origin in prod, and the save silently fails
  updateSiteSetting: (key: string, value: string) =>
    adminApiRequest<{
      ok: boolean; key: string; value: string; data_type: string;
      old_value: string | null; default: string; live: boolean; danger: boolean;
      propagation_seconds: number;
    }>(
      `/site-settings/${encodeURIComponent(key)}/`,
      { method: 'PUT', body: JSON.stringify({ value }) }
    ),

  /** Every recorded change to one setting, newest first, actor handle joined. */
  getSiteSettingHistory: (key: string, limit = 20) =>
    adminApiRequest<{ key: string; rows: SiteSettingHistoryRow[] }>(
      `/site-settings/${encodeURIComponent(key)}/history/?limit=${limit}`
    ),

  // ---- sponsored accounts ----
  // POST/PATCH responses carry no aggregates (active_posts, posts_total,
  // karma_paid, engagements come back as 0) — only listSponsors computes them.
  listSponsors: () => adminApiRequest<SponsorRow[]>(`/sponsors/`),
  addSponsor: (xUsername: string, karmaPerPost: number, notes = '') =>
    adminApiRequest<SponsorRow>(`/sponsors/`, {
      method: 'POST',
      body: JSON.stringify({ x_username: xUsername, karma_per_post: karmaPerPost, notes }),
    }),
  updateSponsor: (sponsorId: string, patch: SponsorPatch) =>
    adminApiRequest<SponsorRow>(`/sponsors/${sponsorId}/`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteSponsor: (sponsorId: string) =>
    adminApiRequest<{ ok: boolean }>(`/sponsors/${sponsorId}/`, { method: 'DELETE' }),

  // ---- operations ----
  opsHealth: () => adminApiRequest<OpsHealth>(`/ops/health/`),
  opsBatches: (status = '', limit = 25, offset = 0) =>
    adminApiRequest<OpsPage<OpsBatch>>(`/ops/batches/?status=${status}&limit=${limit}&offset=${offset}`),
  opsRequeueBatch: (batchId: string) =>
    adminApiRequest<{ ok: boolean; status: string; queued: boolean }>(
      `/ops/batches/${batchId}/requeue/`, { method: 'POST', body: JSON.stringify({}) }),
  opsOutbox: (status = 'failed', limit = 25, offset = 0) =>
    adminApiRequest<OpsPage<OpsNotification>>(`/ops/outbox/?status=${status}&limit=${limit}&offset=${offset}`),
  opsRetryNotification: (eventId: string) =>
    adminApiRequest<{ ok: boolean; status: string }>(
      `/ops/outbox/${eventId}/retry/`, { method: 'POST', body: JSON.stringify({}) }),
  opsAudit: (q: { action?: string; actor?: string; limit?: number; offset?: number } = {}) =>
    adminApiRequest<OpsPage<OpsAuditRow> & { actions: string[] }>(
      `/ops/audit/?action=${encodeURIComponent(q.action || '')}&actor=${encodeURIComponent(q.actor || '')}` +
      `&limit=${q.limit ?? 50}&offset=${q.offset ?? 0}`),
  opsPosts: (status = 'active', q = '', limit = 25, offset = 0) =>
    adminApiRequest<OpsPage<OpsPost>>(
      `/ops/posts/?status=${status}&q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`),
  opsCancelPost: (postId: string, reason: string) =>
    adminApiRequest<{ ok: boolean; refunded: number }>(
      `/ops/posts/${postId}/cancel/`, { method: 'POST', body: JSON.stringify({ reason }) }),
};

export interface OpsPage<T> { items: T[]; total: number; limit: number; offset: number }

export interface OpsHealth {
  gateway: { configured: boolean; reachable: boolean; credits: number | null; error: string | null; checked_at: string | null };
  sponsor_feed: { stream_enabled: boolean; active_sponsors: number; last_poll_at: string | null; minutes_since_poll: number | null; poll_seconds: number };
  batches: { pending: number; processing: number; failed: number; held: number; failed_24h: number; oldest_waiting_minutes: number | null };
  outbox: { pending: number; processing: number; failed: number };
}

export interface OpsBatch {
  id: string; user_id: string; user_handle: string | null; status: string; held: boolean;
  engagements: number; passed: number | null; failed: number | null; credits_awarded: number | null;
  message: string; age_minutes: number; created_at: string | null; completed_at: string | null;
}

export interface OpsNotification {
  id: string; event_type: string; status: string; retry_count: number; max_retries: number;
  error_message: string; telegram_id: number | null; payload: Record<string, unknown>;
  created_at: string | null; next_retry_at: string | null;
}

export interface OpsAuditRow {
  id: string; action: string; target_type: string; target_id: string | null;
  detail: Record<string, unknown>; actor_id: string | null; actor_handle: string | null;
  actor_role: string | null; created_at: string | null;
}

export interface OpsPost {
  id: string; x_link: string; tweet_text: string; author: string | null; poster_id: string;
  poster_handle: string | null; is_sponsored: boolean; status: string; escrow: number;
  initial_escrow: number; engagements: number; created_at: string | null;
}

// URL normalization helper for frontend validation
export function normalizeXLink(url: string): {
  valid: boolean;
  normalized: string;
  tweetId: string;
  username: string;
  error?: string;
} {
  // Strip protocol
  let clean = url.replace(/^https?:\/\//, '');

  // Reject i/status (anonymous links)
  if (clean.includes('/i/status/')) {
    return {
      valid: false,
      normalized: '',
      tweetId: '',
      username: '',
      error: 'Anonymous links not accepted. Use link with username.',
    };
  }

  // Extract username and tweet ID
  const match = clean.match(/(?:x\.com|twitter\.com)\/([^\/]+)\/status\/(\d+)/);
  if (!match) {
    return {
      valid: false,
      normalized: '',
      tweetId: '',
      username: '',
      error: 'Invalid link format. Use: x.com/username/status/...',
    };
  }

  const [, username, tweetId] = match;

  // Reject if username is 'i' or 'intent'
  if (['i', 'intent', 'share', 'search'].includes(username.toLowerCase())) {
    return {
      valid: false,
      normalized: '',
      tweetId: '',
      username: '',
      error: 'Invalid link format',
    };
  }

  // Build normalized URL (no query params)
  const normalized = `https://x.com/${username}/status/${tweetId}`;

  return { valid: true, normalized, tweetId, username };
}
