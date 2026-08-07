import { lookupProfile, type Profile } from "./mock";

// ── Loudrr API client ────────────────────────────────────────────────────────
// Goes LIVE automatically when NEXT_PUBLIC_LOUDRR_API is set (e.g. https://api.loudrr.com/v1):
//   GET {API}/score?userName=X            -> { score, raw, smart_followers, userName, userId }
//   GET {API}/smart-engagement?userName=X -> { counts: {"YYYY-MM-DD": n}, coverage, ... }
//   GET {API}/kol-calls?window=24h        -> { items: [...] }
// Score + engagement are fetched INDEPENDENTLY (allSettled): a dead engagement endpoint can
// never break the score card, and vice versa. Failures fall back to the seeded mock ONLY on
// fields where showing mock is honest (never a fake heatmap on a live deployment).

const API = process.env.NEXT_PUBLIC_LOUDRR_API ?? "";
export const USE_LIVE = !!API;

async function fetchJson(path: string): Promise<Record<string, unknown> | null> {
  if (!API) return null;
  const res = await fetch(`${API}${path}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json();
}

export type EngagementData = {
  counts: Record<string, number>;
  /** per-day fraction (0..1) of the tracked panel whose fetched history covers that day —
   *  low-coverage days must render as "partial tracking", never as low activity */
  panelCoverage: Record<string, number>;
  trackedSince: string | null;
  panelSize: number;
};

async function fetchEngagement(clean: string): Promise<EngagementData | null> {
  const data = await fetchJson(`/smart-engagement?userName=${encodeURIComponent(clean)}`);
  const counts = data?.counts as Record<string, number> | undefined;
  if (data?.coverage === "tracked" && counts && typeof counts === "object") {
    return {
      counts,
      panelCoverage: (data?.panelCoverage as Record<string, number>) ?? {},
      trackedSince: (data?.trackedSince as string | null) ?? null,
      panelSize: (data?.panelSize as number) ?? 0,
    };
  }
  return null;
}

type ApiProfile = {
  found: boolean; rank: number; userName: string; name: string | null; score: number;
  eliteFollowers: number | null; followers: number | null; following: number | null;
  verified: boolean; categories: string | null; bio: string | null; percentile: number;
  totalRanked: number;
  notableFollowers: { userName: string; name: string | null; score: number; rank: number; categories: string | null }[];
};

/** Live profile built ONLY from real crawl data — sections without real data stay empty
 *  and the dashboard hides them. Returns null when the account isn't in the ranked set. */
function realProfile(p: ApiProfile, eng: EngagementData | null): Profile {
  const cats = (p.categories || "")
    .split(",").map((c) => c.trim())
    .filter((c) => c && c.toLowerCase() !== "no category" && c.toLowerCase() !== "no categories");
  return {
    live: true,
    handle: p.userName,
    name: p.name || p.userName,
    score: p.score,
    rank: p.rank,
    percentile: p.percentile,
    followers: p.followers ?? 0,
    following: p.following ?? 0,
    eliteFollowers: p.eliteFollowers ?? 0,
    category: "crypto",
    verified: p.verified,
    categoriesLabel: cats,
    scoreHistory: [],                      // no real history yet -> chart panels hidden
    windowedDeltas: {} as Profile["windowedDeltas"],
    rankByWindow: {} as Profile["rankByWindow"],
    smartFollowerRatio: p.followers ? Math.min(1, (p.eliteFollowers ?? 0) / p.followers) : 0,
    deltaSmartFollowers: 0,
    followersByCategory: {} as Profile["followersByCategory"],
    topFollowers: p.notableFollowers.map((f) => ({
      handle: f.userName,
      name: f.name || f.userName,
      score: f.score,
      role: "influencers" as const,
      daysAgo: 0,
    })),
    engagement: { engagementRatePct: 0, avgLikes: 0, avgRetweets: 0, avgReplies: 0, followerRatio: 0, botPct: 0 },
    mentions: { d1: 0, d7: 0, d30: 0, uniqueMentioners: 0 },
    mindsharePct: 0,
    mindshareDeltaBps: 0,
    friendshipEvents: [],
    meta: { bio: p.bio || "", location: "", joinYear: 0, renamedCount: 0 },
    sentiment: { label: "neutral", score: 0 },
    engagementDaily: eng?.counts ?? {},
    engagementCoverage: eng?.panelCoverage ?? {},
    engagementTrackedSince: eng?.trackedSince ?? null,
    engagementPanelSize: eng?.panelSize ?? 0,
  };
}

export async function getScore(userName: string): Promise<Profile | null> {
  const clean = userName.trim().replace(/^@/, "");

  if (USE_LIVE) {
    const [profRes, engRes] = await Promise.allSettled([
      fetchJson(`/profile?userName=${encodeURIComponent(clean)}`),
      fetchEngagement(clean),
    ]);
    const data = profRes.status === "fulfilled" ? (profRes.value as ApiProfile | null) : null;
    const eng = engRes.status === "fulfilled" ? engRes.value : null;
    // Live = REAL ONLY. Unknown handle -> null (honest "not tracked" state), never a
    // synthetic profile on the public funnel.
    if (!data?.found) return null;
    return realProfile(data, eng);
  }

  // mock path (no API configured — local design work only)
  await new Promise((r) => setTimeout(r, 480 + Math.random() * 420));
  return lookupProfile(clean);
}

// ── leaderboard + search (real ranked universe) ─────────────────────────────

export type LeaderboardItem = {
  rank: number; userName: string; name: string | null; score: number;
  eliteFollowers: number | null; followers: number | null; verified: boolean;
  categories: string | null;
};

export async function getLeaderboard(limit = 50, offset = 0):
  Promise<{ total: number; items: LeaderboardItem[] } | null> {
  try {
    const data = await fetchJson(`/leaderboard?limit=${limit}&offset=${offset}`);
    if (!Array.isArray(data?.items)) return null;
    return { total: (data!.total as number) ?? 0, items: data!.items as LeaderboardItem[] };
  } catch {
    return null;
  }
}

export async function searchAccounts(q: string): Promise<LeaderboardItem[]> {
  try {
    const data = await fetchJson(`/search?q=${encodeURIComponent(q)}`);
    return Array.isArray(data?.items) ? (data!.items as LeaderboardItem[]) : [];
  } catch {
    return [];
  }
}

// ── KOL calls ────────────────────────────────────────────────────────────────

export type KolCallsItem = {
  contract: string; chain: string | null; symbol: string | null; name: string | null;
  image: string | null; priceUsd: number | null; mcapUsd: number | null;
  change24h: number | null; calls: number; kols: number;
  kolSample: { username: string; count: number }[];
};

export type KolTokenCall = {
  username: string | null; tweetId: string; ts: string | null;
  priceAtCall: number | null; confidence: string;
};

export async function getKolCalls(window: string): Promise<KolCallsItem[] | null> {
  try {
    const data = await fetchJson(`/kol-calls?window=${encodeURIComponent(window)}`);
    return Array.isArray(data?.items) ? (data!.items as KolCallsItem[]) : null;
  } catch {
    return null;
  }
}

export async function getKolTokenCalls(contract: string): Promise<KolTokenCall[]> {
  try {
    const data = await fetchJson(`/kol-calls/token?contract=${encodeURIComponent(contract)}`);
    return Array.isArray(data?.calls) ? (data!.calls as KolTokenCall[]) : [];
  } catch {
    return [];
  }
}

export type KolToken = {
  contract: string; chain: string | null; symbol: string | null; name: string | null;
  image: string | null; priceUsd: number | null; mcapUsd: number | null; change24h: number | null;
};

export async function getKolToken(
  contract: string,
): Promise<{ token: KolToken | null; calls: KolTokenCall[] }> {
  try {
    const data = await fetchJson(`/kol-calls/token?contract=${encodeURIComponent(contract)}`);
    return {
      token: (data?.token as KolToken) ?? null,
      calls: Array.isArray(data?.calls) ? (data!.calls as KolTokenCall[]) : [],
    };
  } catch {
    return { token: null, calls: [] };
  }
}

export type ChartTimeframe = "5m" | "15m" | "1h" | "4h" | "1d";

/** Real OHLCV bars. `price` == close, kept so any line-chart caller still works. */
export type Candle = {
  ts: number; open: number; high: number; low: number; close: number; volume: number;
  price: number;
};

export type KolChart = {
  candles: Candle[];
  calls: { username: string | null; tweetId: string; ts: string | null; priceAtCall: number | null }[];
  rides: {
    username: string | null; side: string; ts: string | null;
    price: number | null; volumeUsd: number | null;
    score: number | null; tier: string | null; smartFollowers: number | null;
  }[];
  timeframe: ChartTimeframe;
};

export async function getKolChart(contract: string, timeframe: ChartTimeframe = "4h"): Promise<KolChart> {
  try {
    const data = await fetchJson(
      `/kol-calls/chart?contract=${encodeURIComponent(contract)}&timeframe=${timeframe}`,
    );
    return {
      candles: Array.isArray(data?.candles) ? (data!.candles as KolChart["candles"]) : [],
      calls: Array.isArray(data?.calls) ? (data!.calls as KolChart["calls"]) : [],
      rides: Array.isArray(data?.rides) ? (data!.rides as KolChart["rides"]) : [],
      timeframe: (data?.timeframe as ChartTimeframe) ?? timeframe,
    };
  } catch {
    return { candles: [], calls: [], rides: [], timeframe };
  }
}

// ── live trade tape (the "X bought $Y just now" ticker under the chart) ──

export type TapeTrade = {
  wallet: string; handle: string | null; isKol: boolean;
  score: number | null; tier: string | null;
  side: string; volumeUsd: number | null; priceUsd: number | null; ts: string | null;
};

export async function getKolTape(contract: string, limit = 30): Promise<TapeTrade[]> {
  try {
    const data = await fetchJson(
      `/kol-calls/tape?contract=${encodeURIComponent(contract)}&limit=${limit}`,
    );
    return Array.isArray(data?.trades) ? (data!.trades as TapeTrade[]) : [];
  } catch {
    return [];
  }
}

// ── Social Buzz (X-activity, score-weighted — the offchain counterpart to KOL Rides) ──

export type BuzzBucket = { ts: string; buzz: number; topCallers: string[] };
export type KolSignal = {
  username: string; score: number; tier: string; followers: number; signals: number;
};
export type KolBuzz = {
  buzzIndex: number;
  series: BuzzBucket[];
  signals: KolSignal[];
  coverage: string;
};

export async function getKolBuzz(contract: string, window: "7d" | "30d" = "7d"): Promise<KolBuzz> {
  try {
    const data = await fetchJson(
      `/kol-calls/buzz?contract=${encodeURIComponent(contract)}&window=${window}`,
    );
    return {
      buzzIndex: typeof data?.buzzIndex === "number" ? (data.buzzIndex as number) : 0,
      series: Array.isArray(data?.series) ? (data!.series as BuzzBucket[]) : [],
      signals: Array.isArray(data?.signals) ? (data!.signals as KolSignal[]) : [],
      coverage: (data?.coverage as string) ?? "none",
    };
  } catch {
    return { buzzIndex: 0, series: [], signals: [], coverage: "none" };
  }
}
