// Mock data for the Loudrr UI. Seeded with the REAL calibrated Loudrr Scores from the
// scoring engine (Elon 5853, Vitalik 4864, …) and augmented with deterministic, hash-seeded
// depth (history, role breakdown, engagement, mindshare, …) so the dashboard is dense and
// stable across SSR/CSR. Swapping in the live GET /score?userName= endpoint stays a one-line
// change in lib/api.ts — the live/keyed tiers fill these same fields.

// ── time windows ─────────────────────────────────────────────────────────────
export const WINDOWS = [
  { key: "24h", label: "24H", days: 1 },
  { key: "7d", label: "7D", days: 7 },
  { key: "30d", label: "30D", days: 30 },
  { key: "3m", label: "3M", days: 90 },
  { key: "1y", label: "1Y", days: 364 },
] as const;
export type WindowKey = (typeof WINDOWS)[number]["key"];

// ── follower roles (who confers your score) ──────────────────────────────────
export const ROLES = ["influencers", "projects", "funds", "founders", "angels", "media", "exchanges"] as const;
export type Role = (typeof ROLES)[number];
export const ROLE_LABEL: Record<Role, string> = {
  influencers: "Influencers",
  projects: "Projects",
  funds: "Funds / VCs",
  founders: "Founders",
  angels: "Angels",
  media: "Media",
  exchanges: "Exchanges",
};
// harmonious categorical scale anchored on the brand orange + iris
export const ROLE_COLOR: Record<Role, string> = {
  influencers: "#ff6a00",
  projects: "#8b8cf0",
  funds: "#2bb6c4",
  founders: "#ff5da8",
  angels: "#ffc24b",
  media: "#56b6ff",
  exchanges: "#9b8cff",
};

export type Delta = { abs: number; relPct: number };
export type Receipt = { handle: string; name: string; score: number; role: Role };
export type FriendshipEvent = {
  type: "follow" | "unfollow";
  handle: string;
  name: string;
  score: number;
  role: Role;
  daysAgo: number;
};

export type Profile = {
  handle: string;
  name: string;
  score: number;
  rank: number;
  percentile: number;
  followers: number;
  following: number;
  eliteFollowers: number;
  category: "crypto";
  verified?: boolean;

  // depth
  scoreHistory: number[]; // 365 daily points, last = current
  windowedDeltas: Record<WindowKey, Delta>;
  rankByWindow: Record<WindowKey, { rank: number; delta: number }>; // delta>0 = climbed
  smartFollowerRatio: number; // 0..1
  deltaSmartFollowers: number; // Δ SF over 7d
  followersByCategory: Record<Role, number>;
  topFollowers: Receipt[];
  engagement: {
    engagementRatePct: number;
    avgLikes: number;
    avgRetweets: number;
    avgReplies: number;
    followerRatio: number;
    botPct: number;
  };
  mentions: { d1: number; d7: number; d30: number; uniqueMentioners: number };
  mindsharePct: number;
  mindshareDeltaBps: number;
  friendshipEvents: FriendshipEvent[];
  meta: { bio: string; location: string; joinYear: number; renamedCount: number; website?: string };
  sentiment: { label: "bullish" | "neutral" | "bearish"; score: number };
  /** LIVE smart-engagement series from /v1/smart-engagement (sparse, date-keyed).
   *  Absent on the mock path — the heatmap then renders its deterministic dev fallback. */
  engagementDaily?: Record<string, number>;
  /** per-day fraction of the tracked panel whose fetched history covers that day (0..1) —
   *  the heatmap renders low-coverage days as "partial tracking", never as low activity. */
  engagementCoverage?: Record<string, number>;
  /** earliest day with >=90% panel coverage (full-tracking boundary), ISO date. */
  engagementTrackedSince?: string | null;
  /** number of tracked smart accounts in the engagement panel. */
  engagementPanelSize?: number;
  /** true when built purely from real crawl data — dashboard hides sections with no data. */
  live?: boolean;
  /** real vendor-corroborated categories (e.g. ["Influencers"]) when live. */
  categoriesLabel?: string[];
};

export function avatar(handle: string) {
  return `https://unavatar.io/x/${handle}`;
}

// ── deterministic seeding ────────────────────────────────────────────────────
function hashStr(s: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

const BIOS = [
  "building at the edge of crypto + culture",
  "onchain since the last cycle. probably nothing.",
  "researching protocols, markets, and attention",
  "ex-tradfi. now permanently online.",
  "shipping. occasionally posting.",
  "liquidity is a state of mind",
  "decentralizing things, one thread at a time",
  "founder · investor · perpetually early",
];
const LOCATIONS = ["San Francisco", "New York", "London", "Singapore", "Dubai", "Lisbon", "Remote", "Onchain"];

// ── depth generation ─────────────────────────────────────────────────────────
const LOOKBACK: Record<WindowKey, number> = { "24h": 1, "7d": 7, "30d": 30, "3m": 90, "1y": 364 };

function buildHistory(score: number, rng: () => number): number[] {
  const N = 365;
  const out = new Array(N);
  out[N - 1] = score;
  // random-walk backward with mild mean-reversion + occasional step events
  let cur = score;
  for (let i = N - 2; i >= 0; i--) {
    const drift = (score - cur) * 0.004; // pull toward the present level going back
    const noise = (rng() - 0.5) * score * 0.018;
    const step = rng() < 0.04 ? (rng() - 0.5) * score * 0.06 : 0;
    cur = clamp(cur - drift - noise - step, Math.max(40, score * 0.45), score * 1.18);
    out[i] = Math.round(cur);
  }
  out[N - 1] = score;
  return out;
}

function deltasFromHistory(h: number[]): Record<WindowKey, Delta> {
  const now = h[h.length - 1];
  const out = {} as Record<WindowKey, Delta>;
  (Object.keys(LOOKBACK) as WindowKey[]).forEach((w) => {
    const prior = h[h.length - 1 - LOOKBACK[w]] ?? h[0];
    const abs = now - prior;
    out[w] = { abs, relPct: prior ? (abs / prior) * 100 : 0 };
  });
  return out;
}

function splitByRole(elite: number, score: number, rng: () => number): Record<Role, number> {
  // higher-score accounts skew toward funds/projects/founders; lower skew toward influencers
  const tilt = clamp(score / 6000, 0, 1);
  const base: Record<Role, number> = {
    influencers: 0.34 - tilt * 0.16,
    projects: 0.16 + tilt * 0.05,
    funds: 0.08 + tilt * 0.12,
    founders: 0.12 + tilt * 0.04,
    angels: 0.1,
    media: 0.12 - tilt * 0.03,
    exchanges: 0.08 - tilt * 0.02,
  };
  const weights = ROLES.map((r) => Math.max(0.02, base[r] * (0.75 + rng() * 0.5)));
  const sum = weights.reduce((a, b) => a + b, 0);
  const out = {} as Record<Role, number>;
  let acc = 0;
  ROLES.forEach((r, i) => {
    const v = i === ROLES.length - 1 ? elite - acc : Math.round((weights[i] / sum) * elite);
    out[r] = Math.max(0, v);
    acc += out[r];
  });
  return out;
}

// ── seed set (real calibrated scores) ────────────────────────────────────────
type Seed = {
  handle: string;
  name: string;
  score: number;
  followers: number;
  eliteFollowers: number;
  verified?: boolean;
};
const RAW: Seed[] = [
  { handle: "elonmusk", name: "Elon Musk", score: 5853, followers: 240_392_890, eliteFollowers: 19854, verified: true },
  { handle: "VitalikButerin", name: "Vitalik Buterin", score: 4864, followers: 5_610_220, eliteFollowers: 19216, verified: true },
  { handle: "cz_binance", name: "CZ BNB", score: 4743, followers: 9_240_110, eliteFollowers: 18402, verified: true },
  { handle: "brian_armstrong", name: "Brian Armstrong", score: 4690, followers: 1_420_330, eliteFollowers: 16880, verified: true },
  { handle: "balajis", name: "Balaji", score: 4612, followers: 1_010_900, eliteFollowers: 15930, verified: true },
  { handle: "punk6529", name: "6529", score: 4507, followers: 540_220, eliteFollowers: 14110, verified: true },
  { handle: "karpathy", name: "Andrej Karpathy", score: 4413, followers: 1_080_400, eliteFollowers: 8459, verified: true },
  { handle: "saylor", name: "Michael Saylor", score: 4355, followers: 3_980_120, eliteFollowers: 11240, verified: true },
  { handle: "cdixon", name: "Chris Dixon", score: 4221, followers: 880_330, eliteFollowers: 13980, verified: true },
  { handle: "naval", name: "Naval", score: 4180, followers: 2_640_900, eliteFollowers: 11222, verified: true },
  { handle: "cobie", name: "Cobie", score: 4098, followers: 800_120, eliteFollowers: 12260, verified: true },
  { handle: "pmarca", name: "Marc Andreessen", score: 4042, followers: 1_390_200, eliteFollowers: 11736, verified: true },
  { handle: "jack", name: "jack", score: 3995, followers: 6_700_540, eliteFollowers: 10765, verified: true },
  { handle: "openai", name: "OpenAI", score: 3956, followers: 4_942_671, eliteFollowers: 8180, verified: true },
  { handle: "solana", name: "Solana", score: 3902, followers: 3_110_880, eliteFollowers: 10665, verified: true },
  { handle: "tarunchitra", name: "Tarun Chitra", score: 3744, followers: 90_220, eliteFollowers: 6120 },
  { handle: "zachxbt", name: "ZachXBT", score: 3690, followers: 980_440, eliteFollowers: 9210, verified: true },
  { handle: "greg16676935420", name: "greg", score: 3597, followers: 1_770_827, eliteFollowers: 7876 },
  { handle: "BarackObama", name: "Barack Obama", score: 3563, followers: 131_200_000, eliteFollowers: 5575, verified: true },
  { handle: "RyanSAdams", name: "Ryan Sean Adams", score: 3504, followers: 320_110, eliteFollowers: 6680 },
  { handle: "haydenzadams", name: "Hayden Adams", score: 3471, followers: 280_900, eliteFollowers: 7010, verified: true },
  { handle: "toly", name: "toly", score: 3402, followers: 410_220, eliteFollowers: 7970, verified: true },
  { handle: "waleswoosh", name: "wale.swoosh", score: 3314, followers: 175_209, eliteFollowers: 5426 },
  { handle: "DegenSpartan", name: "DegenSpartan", score: 3208, followers: 220_330, eliteFollowers: 5990 },
  { handle: "TheCryptoLark", name: "Lark Davis", score: 3104, followers: 1_120_000, eliteFollowers: 4480, verified: true },
  { handle: "0xMaki", name: "Maki", score: 3066, followers: 260_770, eliteFollowers: 5210 },
  { handle: "lightcrypto", name: "light", score: 2980, followers: 190_440, eliteFollowers: 4870 },
  { handle: "AltcoinGordon", name: "Gordon", score: 2891, followers: 720_900, eliteFollowers: 3990 },
  { handle: "CryptoCred", name: "Cred", score: 2803, followers: 480_220, eliteFollowers: 4120 },
  { handle: "Rewkang", name: "Andrew Kang", score: 2742, followers: 540_110, eliteFollowers: 4360 },
  { handle: "iamDCinvestor", name: "DCinvestor", score: 2655, followers: 360_880, eliteFollowers: 3880 },
  { handle: "0xfoobar", name: "foobar", score: 2588, followers: 290_120, eliteFollowers: 3990 },
  { handle: "scupytrooples", name: "scupy", score: 2470, followers: 110_330, eliteFollowers: 3110 },
  { handle: "loomdart", name: "loomdart", score: 2399, followers: 230_770, eliteFollowers: 3240 },
  { handle: "0xblest_", name: "blest", score: 962, followers: 9_566, eliteFollowers: 185 },
];

const NAME_BY_HANDLE = new Map(RAW.map((s) => [s.handle.toLowerCase(), s.name]));

function assemble(seed: Seed, rank: number, total: number): Profile {
  const rng = mulberry32(hashStr(seed.handle));
  const history = buildHistory(seed.score, rng);
  const windowedDeltas = deltasFromHistory(history);
  const followersByCategory = splitByRole(seed.eliteFollowers, seed.score, rng);

  const relevantFollowers = Math.max(seed.eliteFollowers, seed.followers * (0.02 + rng() * 0.22));
  const smartFollowerRatio = clamp(seed.eliteFollowers / relevantFollowers, 0.004, 0.62);
  const deltaSmartFollowers = Math.round(
    (windowedDeltas["7d"].abs >= 0 ? 1 : -1) * seed.eliteFollowers * (0.01 + rng() * 0.07),
  );

  // rank movement per window (lightweight, plausible)
  const rankByWindow = {} as Profile["rankByWindow"];
  (Object.keys(LOOKBACK) as WindowKey[]).forEach((w) => {
    const swing = Math.round((windowedDeltas[w].relPct / 100) * (rank + 6) * (0.6 + rng()));
    const prior = clamp(rank + swing, 1, total);
    rankByWindow[w] = { rank, delta: prior - rank };
  });

  const engRate = +(0.4 + rng() * 4.6).toFixed(2); // %
  const engagement = {
    engagementRatePct: engRate,
    avgLikes: Math.round((seed.followers * engRate) / 100 / 12),
    avgRetweets: Math.round((seed.followers * engRate) / 100 / 64),
    avgReplies: Math.round((seed.followers * engRate) / 100 / 90),
    followerRatio: +(0.4 + rng() * 18).toFixed(1),
    botPct: Math.round(2 + rng() * 20),
  };

  const mentions = {
    d30: Math.round(seed.eliteFollowers * (0.4 + rng() * 2.2)),
    d7: 0,
    d1: 0,
    uniqueMentioners: 0,
  };
  mentions.d7 = Math.round(mentions.d30 * (0.18 + rng() * 0.12));
  mentions.d1 = Math.round(mentions.d7 * (0.12 + rng() * 0.16));
  mentions.uniqueMentioners = Math.round(mentions.d30 * (0.4 + rng() * 0.3));

  const sLabel = windowedDeltas["7d"].relPct > 1.5 ? "bullish" : windowedDeltas["7d"].relPct < -1.5 ? "bearish" : "neutral";

  return {
    handle: seed.handle,
    name: seed.name,
    score: seed.score,
    rank,
    percentile: Math.max(50, 100 - (rank / total) * 50 - (rank === 1 ? 0.01 : 0)),
    followers: seed.followers,
    following: Math.round(300 + rng() * 4700),
    eliteFollowers: seed.eliteFollowers,
    category: "crypto",
    verified: seed.verified,
    scoreHistory: history,
    windowedDeltas,
    rankByWindow,
    smartFollowerRatio,
    deltaSmartFollowers,
    followersByCategory,
    topFollowers: [], // filled after all ranks exist
    engagement,
    mentions,
    mindsharePct: 0, // filled after all assembled (needs the population)
    mindshareDeltaBps: Math.round(windowedDeltas["7d"].relPct * (4 + rng() * 8)),
    friendshipEvents: [], // filled after ranks exist
    meta: {
      bio: BIOS[hashStr(seed.handle) % BIOS.length],
      location: LOCATIONS[(hashStr(seed.handle) >> 3) % LOCATIONS.length],
      joinYear: 2011 + ((hashStr(seed.handle) >> 5) % 12),
      renamedCount: (hashStr(seed.handle) >> 2) % 5,
      website: rng() < 0.45 ? `${seed.handle.toLowerCase()}.xyz` : undefined,
    },
    sentiment: { label: sLabel, score: +clamp(windowedDeltas["7d"].relPct / 10, -1, 1).toFixed(2) },
  };
}

const sorted = [...RAW].sort((a, b) => b.score - a.score);
export const LEADERBOARD: Profile[] = sorted.map((s, i) => assemble(s, i + 1, sorted.length));

// receipts + friendship events (need the ranked set to exist)
function pickPeers(idx: number, rng: () => number): Receipt[] {
  const out: Receipt[] = [];
  const pool = LEADERBOARD.filter((_, i) => i !== idx);
  const n = clamp(Math.round(10 - idx / 5), 4, 10);
  const roles = ROLES;
  for (let i = 0; i < n; i++) {
    const p = pool[Math.floor(rng() * Math.min(pool.length, idx + 6))] ?? pool[i % pool.length];
    if (!p) continue;
    out.push({ handle: p.handle, name: p.name, score: p.score, role: roles[(hashStr(p.handle) + i) % roles.length] });
  }
  // unique by handle
  return Array.from(new Map(out.map((r) => [r.handle, r])).values());
}

function buildMindshare() {
  const recencyWeighted = LEADERBOARD.map((p) => p.score * (1 + p.windowedDeltas["7d"].relPct / 100));
  const total = recencyWeighted.reduce((a, b) => a + b, 0);
  LEADERBOARD.forEach((p, i) => {
    p.mindsharePct = +((recencyWeighted[i] / total) * 100).toFixed(2);
  });
}

LEADERBOARD.forEach((p, i) => {
  const rng = mulberry32(hashStr(p.handle) ^ 0x9e3779b9);
  p.topFollowers = pickPeers(i, rng).sort((a, b) => b.score - a.score);
  // friendship events: recent follows from higher-ranked accounts, a few unfollows
  const events: FriendshipEvent[] = [];
  const m = clamp(Math.round(12 - i / 6), 6, 14);
  for (let k = 0; k < m; k++) {
    const peer = LEADERBOARD[Math.floor(rng() * LEADERBOARD.length)];
    if (!peer || peer.handle === p.handle) continue;
    events.push({
      type: rng() < 0.78 ? "follow" : "unfollow",
      handle: peer.handle,
      name: peer.name,
      score: peer.score,
      role: ROLES[(hashStr(peer.handle) + k) % ROLES.length],
      daysAgo: Math.round(rng() * 88) + 1,
    });
  }
  p.friendshipEvents = events.sort((a, b) => a.daysAgo - b.daysAgo);
});
buildMindshare();

const BY_HANDLE = new Map(LEADERBOARD.map((p) => [p.handle.toLowerCase(), p]));

// ── synthetic profile for any handle not in the seed set ─────────────────────
export function syntheticProfile(handle: string): Profile {
  const clean = handle.replace(/^@/, "");
  const h = hashStr(clean.toLowerCase());
  const rng = mulberry32(h);
  const score = 380 + (h % 2600);
  const total = LEADERBOARD.length + 41000;
  const rank = LEADERBOARD.length + 1 + (h % 41000);
  const seed: Seed = {
    handle: clean,
    name: NAME_BY_HANDLE.get(clean.toLowerCase()) ?? clean,
    score,
    followers: 600 + (h % 250000),
    eliteFollowers: 12 + (h % 900),
  };
  const p = assemble(seed, rank, total);
  p.percentile = Math.max(20, 92 - (h % 70));
  // light receipts/events from the leaderboard so synthetic profiles aren't empty
  const peers = LEADERBOARD.slice(5, 5 + 6).map((q, i) => ({
    handle: q.handle,
    name: q.name,
    score: q.score,
    role: ROLES[(hashStr(q.handle) + i) % ROLES.length] as Role,
  }));
  p.topFollowers = peers;
  p.friendshipEvents = peers.slice(0, 5).map((q, i) => ({
    type: "follow" as const,
    handle: q.handle,
    name: q.name,
    score: q.score,
    role: q.role,
    daysAgo: 4 + i * 9 + (h % 7),
  }));
  p.mindsharePct = +(0.0005 + (rng() * 0.02)).toFixed(3);
  return p;
}

export function lookupProfile(handle: string): Profile {
  return BY_HANDLE.get(handle.replace(/^@/, "").toLowerCase()) ?? syntheticProfile(handle);
}

export const STATS = {
  voters: 98303,
  edges: 91_218_648,
  scoreable: 4_081_077,
  agreementSorsa: 0.893,
  medianScore: 1240,
  lastRefreshedMinsAgo: 14,
};

export const CATEGORIES = [
  { key: "crypto", label: "Crypto", live: true },
  { key: "ai", label: "AI / Tech", live: false },
  { key: "stocks", label: "Stocks", live: false },
];
