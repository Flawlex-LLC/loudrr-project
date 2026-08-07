import { fmtCompact } from "@/lib/score";

type Eng = {
  engagementRatePct: number;
  avgLikes: number;
  avgRetweets: number;
  avgReplies: number;
  followerRatio: number;
  botPct: number;
};

function rateTier(pct: number) {
  if (pct >= 3) return { label: "Excellent", color: "#3fd6a0" };
  if (pct >= 1.5) return { label: "Strong", color: "#56b6ff" };
  if (pct >= 0.5) return { label: "Solid", color: "#ffc24b" };
  return { label: "Low", color: "#ff8a33" };
}
function botTier(pct: number) {
  if (pct <= 6) return { label: "Clean", color: "#3fd6a0" };
  if (pct <= 12) return { label: "Healthy", color: "#56b6ff" };
  if (pct <= 18) return { label: "Some bots", color: "#ffc24b" };
  return { label: "Noisy", color: "#ff5d52" };
}

// Sorsa-style engagement/quality grid with named tier labels. Server-safe.
export function EngagementPanel({ engagement }: { engagement: Eng }) {
  const rt = rateTier(engagement.engagementRatePct);
  const bt = botTier(engagement.botPct);
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <Cell label="Engagement rate" value={`${engagement.engagementRatePct.toFixed(2)}%`} tier={rt} />
      <Cell label="Avg likes" value={fmtCompact(engagement.avgLikes)} />
      <Cell label="Avg reposts" value={fmtCompact(engagement.avgRetweets)} />
      <Cell label="Avg replies" value={fmtCompact(engagement.avgReplies)} />
      <Cell label="Follower ratio" value={`${engagement.followerRatio.toFixed(1)}×`} />
      <Cell label="Bot followers" value={`${engagement.botPct}%`} tier={bt} />
    </div>
  );
}

function Cell({ label, value, tier }: { label: string; value: string; tier?: { label: string; color: string } }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-ink-800/40 p-3">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-bone-500">{label}</div>
      <div className="mt-1 font-mono text-xl font-bold tabular-nums text-bone-100">{value}</div>
      {tier && (
        <div className="mt-1 inline-flex items-center gap-1 font-mono text-[10px]" style={{ color: tier.color }}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: tier.color }} />
          {tier.label}
        </div>
      )}
    </div>
  );
}
