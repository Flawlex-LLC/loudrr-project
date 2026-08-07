// Loudrr Score domain helpers. Scale ~0–6000 (Sorsa-like, slightly higher per calibration).

export const SCORE_MAX = 6000;

export type Tier = {
  name: string;
  min: number;
  color: string; // hex for chips/rings
  blurb: string;
};

// Loudness-themed tiers — ties the score to the brand (signal/volume).
export const TIERS: Tier[] = [
  { name: "Megaphone", min: 4500, color: "#f95400", blurb: "Top-of-feed gravity. The room turns when they post." },
  { name: "Amplifier", min: 3400, color: "#ff7a1a", blurb: "Reliably moves the conversation." },
  { name: "Signal", min: 2300, color: "#ff9500", blurb: "Cuts through the noise consistently." },
  { name: "Echo", min: 1200, color: "#9aa0aa", blurb: "Heard within the circles that matter." },
  { name: "Whisper", min: 0, color: "#7c7c84", blurb: "Emerging — just starting to get noticed." },
];

export function tierFor(score: number): Tier {
  return TIERS.find((t) => score >= t.min) ?? TIERS[TIERS.length - 1];
}

export function fmtScore(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}

export function fmtCompact(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(n >= 10_000 ? 0 : 1) + "K";
  return String(n);
}

// percentile -> human label
export function percentileLabel(p: number): string {
  if (p >= 99.9) return "Top 0.1%";
  if (p >= 99) return "Top 1%";
  if (p >= 95) return "Top 5%";
  if (p >= 90) return "Top 10%";
  if (p >= 75) return "Top 25%";
  return `Top ${(100 - Math.floor(p))}%`;
}

// progress through the current tier toward the next one up
export function tierProgress(score: number): {
  current: Tier;
  next: Tier | null;
  pointsToNext: number;
  pctThroughTier: number;
} {
  const idx = TIERS.findIndex((t) => score >= t.min);
  const current = TIERS[idx] ?? TIERS[TIERS.length - 1];
  const next = idx > 0 ? TIERS[idx - 1] : null; // TIERS is high→low
  if (!next) return { current, next: null, pointsToNext: 0, pctThroughTier: 1 };
  const span = next.min - current.min;
  const into = score - current.min;
  return {
    current,
    next,
    pointsToNext: Math.max(0, next.min - score),
    pctThroughTier: span > 0 ? Math.min(1, into / span) : 1,
  };
}

export type Badge = { key: string; label: string; hint: string; earned: boolean };

// achievement badges derived (not stored) from rank / ratio / movement / tier
export function deriveBadges(opts: {
  rank: number;
  smartFollowerRatio: number;
  rankDelta7d: number;
  score: number;
  mindsharePct: number;
}): Badge[] {
  const tier = tierFor(opts.score);
  return [
    { key: "tier", label: tier.name, hint: "Current volume tier", earned: true },
    { key: "top100", label: "Top 100", hint: "Ranked inside the top 100 voices", earned: opts.rank <= 100 },
    { key: "top20", label: "Top 20", hint: "Ranked inside the top 20 voices", earned: opts.rank <= 20 },
    {
      key: "smart10",
      label: "10%+ Smart",
      hint: "Smart-follower ratio above the 10% strong-influence benchmark",
      earned: opts.smartFollowerRatio >= 0.1,
    },
    { key: "climber", label: "Climbing", hint: "Gained rank over the last 7 days", earned: opts.rankDelta7d > 0 },
    {
      key: "mindshare",
      label: "Conversation",
      hint: "Holds over 1% share-of-voice in crypto",
      earned: opts.mindsharePct >= 1,
    },
  ];
}
