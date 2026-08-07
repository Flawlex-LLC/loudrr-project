// Pure-SVG inline micro-chart. No deps, no hooks — safe in server components (leaderboard rows).
// Pass a shared min/max so slopes are comparable across rows ("live tape" feel).
export function Sparkline({
  data,
  width = 120,
  height = 32,
  min,
  max,
  color = "auto",
  fill = true,
  dot = true,
  strokeWidth = 1.5,
}: {
  data: number[];
  width?: number;
  height?: number;
  min?: number;
  max?: number;
  color?: string; // "auto" => green/red by direction
  fill?: boolean;
  dot?: boolean;
  strokeWidth?: number;
}) {
  if (!data || data.length < 2) return <svg width={width} height={height} />;
  const lo = min ?? Math.min(...data);
  const hi = max ?? Math.max(...data);
  const span = hi > lo ? hi - lo : 1; // guards flat data + degenerate (Infinity) domains
  const pad = strokeWidth + 1;
  const h = height - pad * 2;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = pad + h - ((v - lo) / span) * h;
    return [x, y] as const;
  });
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const area = `${line} L${width} ${height} L0 ${height} Z`;
  const up = data[data.length - 1] >= data[0];
  const col = color === "auto" ? (up ? "#3fd6a0" : "#ff5d52") : color;
  const last = pts[pts.length - 1];

  return (
    <svg width={width} height={height} className="overflow-visible">
      {fill && <path d={area} fill={col} fillOpacity={0.1} />}
      <path d={line} fill="none" stroke={col} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
      {dot && (
        <>
          <circle cx={last[0]} cy={last[1]} r={2.6} fill={col} />
          <circle cx={last[0]} cy={last[1]} r={2.6} fill={col} opacity={0.35}>
            <animate attributeName="r" from="2.6" to="6" dur="1.6s" repeatCount="indefinite" />
            <animate attributeName="opacity" from="0.35" to="0" dur="1.6s" repeatCount="indefinite" />
          </circle>
        </>
      )}
    </svg>
  );
}
