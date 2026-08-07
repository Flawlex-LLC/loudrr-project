import { SCORE_MAX, TIERS, tierFor, tierProgress } from "@/lib/score";

// Sorsa-style distribution bar: the full 0→SCORE_MAX range with tier bands, a marker pin at
// the account's score, and a "next tier in N pts" readout. Pure markup, server-safe.
export function ScorePositionBar({ score }: { score: number }) {
  const tier = tierFor(score);
  const { next, pointsToNext } = tierProgress(score);
  const pct = Math.min(100, (score / SCORE_MAX) * 100);
  // bands low→high for left-to-right layout
  const bands = [...TIERS].reverse();

  return (
    <div>
      <div className="mb-2 flex items-center justify-between font-mono text-[11px] text-bone-500">
        <span>score distribution</span>
        {next ? (
          <span>
            <span className="text-bone-300 tabular-nums">{pointsToNext.toLocaleString()}</span> to{" "}
            <span style={{ color: next.color }}>{next.name}</span>
          </span>
        ) : (
          <span style={{ color: tier.color }}>top tier reached</span>
        )}
      </div>
      <div className="relative h-3 w-full overflow-hidden rounded-full">
        <div className="absolute inset-0 flex">
          {bands.map((b, i) => {
            const lo = b.min;
            const hi = bands[i + 1]?.min ?? SCORE_MAX;
            const w = ((hi - lo) / SCORE_MAX) * 100;
            return <div key={b.name} style={{ width: `${w}%`, background: b.color, opacity: 0.22 }} />;
          })}
        </div>
        {/* filled portion up to score — liquid-glass orange (matches the primary button) */}
        <div
          className="absolute inset-y-0 left-0 overflow-hidden rounded-full"
          style={{
            width: `${pct}%`,
            background: "linear-gradient(135deg, #ff9500 0%, #f95400 55%, #cc5500 100%)",
            boxShadow: "inset 0 1px 0 rgba(255,255,255,0.5), inset 0 -2px 4px rgba(120,40,0,0.3)",
          }}
        >
          <div
            className="pointer-events-none absolute inset-x-0 top-0 h-1/2"
            style={{ background: "linear-gradient(to bottom, rgba(255,255,255,0.5) 0%, transparent 100%)" }}
          />
        </div>
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[10px] text-bone-600">
        <span>0</span>
        {bands.slice(1).map((b) => (
          <span key={b.name} className="hidden sm:inline">
            {b.min.toLocaleString()}
          </span>
        ))}
        <span>{SCORE_MAX.toLocaleString()}</span>
      </div>
    </div>
  );
}
