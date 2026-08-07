/**
 * Design Mode — Mock API data
 * =============================
 * When NEXT_PUBLIC_DESIGN_MODE=true, the API client (lib/api.ts) serves the
 * responses below instead of calling the Django backend. This lets the whole
 * app be browsed and screenshotted offline, with no backend / DB / Telegram.
 *
 * To enable:   set NEXT_PUBLIC_DESIGN_MODE=true in frontend/.env  (then npm run dev)
 * To disable:  remove it or set to false — app behaves 100% normally.
 *
 * This file ONLY affects design mode. It changes no production behaviour.
 */

export const DESIGN_MODE =
  typeof process !== 'undefined' &&
  process.env.NEXT_PUBLIC_DESIGN_MODE === 'true';

/**
 * Which screen state to simulate. Change this in .env to capture different
 * onboarding screens:
 *   NEXT_PUBLIC_DESIGN_STATE=active        -> fully onboarded, main app (default)
 *   NEXT_PUBLIC_DESIGN_STATE=waitlisted    -> Waitlist Pending screen
 *   NEXT_PUBLIC_DESIGN_STATE=unregistered  -> Waitlist Registration screen
 *   NEXT_PUBLIC_DESIGN_STATE=connect_x     -> Connect X screen
 *   NEXT_PUBLIC_DESIGN_STATE=x_pending     -> X Verification Pending screen
 *   NEXT_PUBLIC_DESIGN_STATE=onboarding    -> Onboarding screen
 */
export const DESIGN_STATE =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_DESIGN_STATE) ||
  'active';

// ---- Mock user (fully onboarded by default) -------------------------------
const mockUser = {
  id: 'mock-user-0001',
  display_name: 'Alex Rivera',
  telegram_username: 'alexrivera',
  credits: 1240,
  daily_earned: 60,
  daily_cap: 160,
  total_engagements: 128,
  tier: 'Based',
  current_streak: 5,
  is_pro: false,
  x_username: 'alexrivera',
  tweetscout_score: 420,
  tweetscout_last_updated: new Date().toISOString(),
  x_followers_count: 8400,
  x_display_name: 'Alex Rivera',
  honesty_score: 48,
  available_posts: 7,
  engaged_today: 3,
  is_whitelisted: true,
  loud_access: true,
  x_verified: DESIGN_STATE !== 'connect_x' && DESIGN_STATE !== 'x_pending',
  pending_claimed_x_username:
    DESIGN_STATE === 'x_pending' ? 'alex_rivera_alt' : null,
  x_verification_pending_review: DESIGN_STATE === 'x_pending',
};

// ---- Mock feed posts ------------------------------------------------------
function mockPost(i: number) {
  const authors = [
    { name: 'Sarah Chen', handle: 'sarahbuilds' },
    { name: 'DeFi Daily', handle: 'defidaily' },
    { name: 'Marcus Lee', handle: 'marcusonchain' },
    { name: 'Web3 Weekly', handle: 'web3weekly' },
    { name: 'Nina Park', handle: 'ninacodes' },
    { name: 'Crypto Cabin', handle: 'cryptocabin' },
    { name: 'Tom Hayes', handle: 'tomhayesx' },
  ];
  const texts = [
    'Just shipped a major update — gm to everyone building through the bear. 🚀',
    'Engagement farming is dead. Real communities win. Here is why 🧵',
    'The best time to build was yesterday. The second best time is now.',
    'New thread on why participation-first ecosystems outperform. Bookmark this.',
    'We hit 10k users this week. Thank you to everyone who showed up. 🧡',
    'Hot take: most "growth hacks" are just consistency in disguise.',
    'Loving the energy in this space lately. Builders are back.',
  ];
  const a = authors[i % authors.length];
  return {
    id: `mock-post-${i}`,
    tweet_id: `19000000000000000${i}`,
    x_link: `https://x.com/${a.handle}/status/19000000000000000${i}`,
    tweet_text: texts[i % texts.length],
    tweet_author_name: a.name,
    tweet_author_username: a.handle,
    tweet_author_avatar: '',
    tweet_media: [],
    tweet_created_at: new Date(Date.now() - i * 3600_000).toISOString(),
    credit_reward: 1,
    is_sponsored: i === 1,
    redirect_token: `mock-token-${i}`,
    platform: 'TELEGRAM',
  };
}

const mockFeed = Array.from({ length: 7 }, (_, i) => mockPost(i + 1));

// ---- Mock Loud projects ---------------------------------------------------
const mockLoudProjects = {
  projects: [
    {
      id: 'mock-loud-1',
      name: 'Nebula Protocol',
      slug: 'nebula-protocol',
      description:
        'Create content about Nebula Protocol and earn from a 50,000 point reward pool.',
      logo_url: '',
      reward_pool: '50,000 points',
      starts_at: new Date(Date.now() - 86400_000 * 3).toISOString(),
      ends_at: new Date(Date.now() + 86400_000 * 5).toISOString(),
      min_tweetscout_score: 100,
      max_submissions_per_user: 5,
      is_active: true,
      user_submission_count: 2,
      eligible: true,
    },
    {
      id: 'mock-loud-2',
      name: 'Aurora Finance',
      slug: 'aurora-finance',
      description:
        'Share your honest take on Aurora Finance. Top contributors get bonus rewards.',
      logo_url: '',
      reward_pool: '25,000 points',
      starts_at: new Date(Date.now() - 86400_000 * 1).toISOString(),
      ends_at: new Date(Date.now() + 86400_000 * 9).toISOString(),
      min_tweetscout_score: 200,
      max_submissions_per_user: 3,
      is_active: true,
      user_submission_count: 0,
      eligible: true,
    },
  ],
};

const mockLeaderboard = {
  project: 'Nebula Protocol',
  entries: [
    { rank: 1, x_username: 'topcreator', display_name: 'Top Creator', total_points: 820, submission_count: 5 },
    { rank: 2, x_username: 'sarahbuilds', display_name: 'Sarah Chen', total_points: 640, submission_count: 4 },
    { rank: 3, x_username: 'marcusonchain', display_name: 'Marcus Lee', total_points: 510, submission_count: 3 },
    { rank: 4, x_username: 'alexrivera', display_name: 'Alex Rivera', total_points: 380, submission_count: 2 },
    { rank: 5, x_username: 'ninacodes', display_name: 'Nina Park', total_points: 240, submission_count: 2 },
  ],
};

// ---- Mock claim history ---------------------------------------------------
const mockClaimHistory = {
  batches: [
    {
      id: 'mock-batch-1',
      status: 'COMPLETED',
      passed: 9,
      failed: 1,
      credits_awarded: 10.8,
      message: '9 of 10 engagements verified',
      created_at: new Date(Date.now() - 7200_000).toISOString(),
    },
  ],
};

/**
 * Sentinel: when getMockResponse returns this, the API client throws an error
 * instead of returning data — used to simulate "user does not exist yet" so
 * the app falls through to the waitlist flow.
 */
export const MOCK_SHOULD_FAIL = Symbol('MOCK_SHOULD_FAIL');

/** True when the simulated user has no account yet (pre-approval states). */
const USER_HAS_NO_ACCOUNT =
  DESIGN_STATE === 'unregistered' || DESIGN_STATE === 'waitlisted';

/**
 * Returns mock data for a given endpoint, or undefined if no mock exists
 * (caller then falls through to the real fetch). May return MOCK_SHOULD_FAIL
 * to make the API client throw (simulating a missing user account).
 */
export function getMockResponse(endpoint: string): unknown | undefined {
  // strip query string for matching
  const path = endpoint.split('?')[0];

  switch (path) {
    case '/settings/':
      return { post_cost_min: 60, post_cost_max: 120 };

    case '/user/':
      // In pre-account states the user does not exist yet — make /user/ fail
      // so the app routes to the waitlist registration / pending screens.
      if (USER_HAS_NO_ACCOUNT) return MOCK_SHOULD_FAIL;
      return mockUser;

    case '/user/stats/':
      // Shape must match the UserStats interface in lib/api.ts:
      // { user, posts, engagements, recent_posts }
      return {
        user: {
          display_name: mockUser.display_name,
          telegram_username: mockUser.telegram_username,
          credits: mockUser.credits,
          tier: mockUser.tier,
          current_streak: mockUser.current_streak,
          total_credits_earned: 1840,
          total_credits_spent: 600,
        },
        posts: {
          total: 12,
          active: 3,
          completed: 9,
        },
        engagements: {
          given: 128,
          received: 64,
        },
        recent_posts: [
          {
            id: 'mock-post-1',
            x_link: 'https://x.com/alexrivera/status/1900000000000000001',
            status: 'ACTIVE',
            escrow_remaining: 42,
            engagement_progress: 58,
            created_at: new Date(Date.now() - 3600_000).toISOString(),
          },
          {
            id: 'mock-post-2',
            x_link: 'https://x.com/alexrivera/status/1900000000000000002',
            status: 'COMPLETED',
            escrow_remaining: 0,
            engagement_progress: 100,
            created_at: new Date(Date.now() - 90000_000).toISOString(),
          },
        ],
      };

    case '/session/start/':
      return { posts: mockFeed, session_id: 'mock-session-1' };

    case '/claims/history/':
      return mockClaimHistory;

    case '/x-oauth/start/':
      return { authorize_url: 'https://x.com/i/oauth2/authorize?mock=1' };

    case '/feature-interest/':
      return { registered: false, success: true };

    case '/waitlist/status/':
      if (DESIGN_STATE === 'waitlisted') {
        return {
          status: 'waitlisted',
          x_username: 'alexrivera',
          submitted_at: new Date(Date.now() - 86400_000).toISOString(),
          referral_code: 'LOUD2K9X',
        };
      }
      if (DESIGN_STATE === 'unregistered') {
        return { status: 'not_registered' };
      }
      return { status: 'approved' };

    case '/waitlist/register/':
      // Mock a successful registration so the form transitions to the
      // Waitlist Pending screen (referral card + share buttons).
      return {
        status: 'registered',
        message: 'You are on the waitlist!',
        referral_code: 'LOUD2K9X',
      };

    // --- Loud API ---
    case '/projects/':
      return mockLoudProjects;

    default:
      // leaderboard: /leaderboard/<slug>/
      if (path.startsWith('/leaderboard/')) return mockLeaderboard;
      // feature interest with query
      if (path.startsWith('/feature-interest')) return { registered: false };
      return undefined;
  }
}
