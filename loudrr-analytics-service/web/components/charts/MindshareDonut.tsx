// Single-account share-of-voice donut. Pure SVG, server-safe.
export function MindshareDonut({
  pct,
  size = 132,
  color = "#ff6a00",
  label = "mindshare",
}: {
  pct: number; // 0..100
  size?: number;
  color?: string;
  label?: string;
}) {
  const stroke = 12;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const frac = Math.min(1, Math.max(0, pct / 100));
  // mindshare is tiny by nature; visually amplify so small shares are still legible,
  // but show the true number in the center.
  const shown = Math.min(1, Math.max(frac, frac > 0 ? 0.03 : 0) + frac * 6);
  const dash = c * shown;

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#34343f" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
          style={{ transition: "stroke-dasharray 1s cubic-bezier(0.16,1,0.3,1)" }}
        />
      </svg>
      <div className="absolute inset-0 grid place-content-center text-center">
        <div className="font-mono text-2xl font-semibold tabular-nums" style={{ color }}>
          {pct >= 1 ? pct.toFixed(1) : pct.toFixed(2)}%
        </div>
        <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-bone-500">{label}</div>
      </div>
    </div>
  );
}
