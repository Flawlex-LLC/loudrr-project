import { ArrowDown, ArrowUp } from "@phosphor-icons/react/dist/ssr";

// Smart-follower ratio vs the 10% "strong influence" benchmark. Pure markup, server-safe.
export function RatioMeter({
  ratio,
  benchmark = 0.1,
  delta,
}: {
  ratio: number; // 0..1
  benchmark?: number;
  delta?: number; // Δ smart followers (signed count)
}) {
  const pct = ratio * 100;
  const strong = ratio >= benchmark;
  // bar domain caps at max(2x benchmark, ratio) so the benchmark tick sits mid-ish
  const domain = Math.max(benchmark * 2.2, ratio * 1.1, 0.04);
  const fillW = Math.min(100, (ratio / domain) * 100);
  const benchLeft = Math.min(100, (benchmark / domain) * 100);
  const col = strong ? "#3fd6a0" : "#ff8a33";

  return (
    <div>
      <div className="flex items-end justify-between">
        <div>
          <div className="font-mono text-2xl font-bold tabular-nums" style={{ color: col }}>
            {pct.toFixed(1)}%
          </div>
          <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-bone-500">smart-follower ratio</div>
        </div>
        {delta !== undefined && (
          <span
            className={`flex items-center gap-0.5 font-mono text-xs tabular-nums ${delta >= 0 ? "text-up" : "text-down"}`}
          >
            {delta >= 0 ? <ArrowUp size={11} weight="bold" /> : <ArrowDown size={11} weight="bold" />}
            {Math.abs(delta).toLocaleString()} /7d
          </span>
        )}
      </div>
      <div className="relative mt-3 h-2 w-full overflow-hidden rounded-full bg-white/5">
        <div className="h-full rounded-full" style={{ width: `${fillW}%`, background: col }} />
        {/* benchmark tick */}
        <div className="absolute inset-y-0 w-px bg-white/40" style={{ left: `${benchLeft}%` }} />
      </div>
      <div className="mt-1.5 font-mono text-[10px] text-bone-600">
        {strong ? (
          <span className="text-up">above</span>
        ) : (
          <span className="text-flare-400">below</span>
        )}{" "}
        the {Math.round(benchmark * 100)}% strong-influence benchmark
      </div>
    </div>
  );
}
