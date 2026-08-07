import { ArrowDown, ArrowUp, Minus } from "@phosphor-icons/react/dist/ssr";

// Rank with a movement indicator over the selected window (delta > 0 = climbed). Server-safe.
export function RankMovement({ rank, delta, showRank = true }: { rank: number; delta: number; showRank?: boolean }) {
  const flat = delta === 0;
  const up = delta > 0;
  return (
    <span className="inline-flex items-center gap-1.5">
      {showRank && <span className="font-mono text-sm tabular-nums text-bone-200">#{rank.toLocaleString()}</span>}
      <span
        className={`inline-flex items-center gap-0.5 font-mono text-xs tabular-nums ${
          flat ? "text-bone-600" : up ? "text-up" : "text-down"
        }`}
      >
        {flat ? <Minus size={11} weight="bold" /> : up ? <ArrowUp size={11} weight="bold" /> : <ArrowDown size={11} weight="bold" />}
        {!flat && Math.abs(delta)}
      </span>
    </span>
  );
}
