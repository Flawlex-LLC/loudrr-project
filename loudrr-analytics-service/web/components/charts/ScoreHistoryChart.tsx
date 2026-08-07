"use client";

import { useEffect, useRef, useState } from "react";

// Responsive area chart of the score over the selected window, with a crosshair cursor and a
// tooltip that follows the pointer. Hand-rolled SVG (no chart lib). `data` is pre-sliced to the
// window; x is "days ago" (we don't depend on Date so SSR stays deterministic).
export function ScoreHistoryChart({
  data,
  color = "#ff6a00",
  height = 240,
}: {
  data: number[];
  color?: string;
  height?: number;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(760);
  const [hi, setHi] = useState<number | null>(null);

  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver((entries) => setW(entries[0].contentRect.width));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  if (!data || data.length < 2) return <div ref={wrap} style={{ height }} />;

  const padX = 8;
  const padTop = 16;
  const padBottom = 22;
  const plotH = height - padTop - padBottom;
  const lo = Math.min(...data) * 0.985;
  const hiVal = Math.max(...data) * 1.015;
  const span = hiVal - lo || 1;
  const n = data.length;

  const x = (i: number) => padX + (i / (n - 1)) * (w - padX * 2);
  const y = (v: number) => padTop + plotH - ((v - lo) / span) * plotH;

  const line = data.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(n - 1).toFixed(1)} ${padTop + plotH} L${x(0).toFixed(1)} ${padTop + plotH} Z`;
  const gridVals = [hiVal, (hiVal + lo) / 2, lo];

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = e.clientX - rect.left - padX;
    const idx = Math.round((rel / (w - padX * 2)) * (n - 1));
    setHi(Math.max(0, Math.min(n - 1, idx)));
  };

  const gid = `sh-${color.replace("#", "")}`;

  return (
    <div ref={wrap} className="relative select-none" style={{ height }}>
      <svg width={w} height={height} onMouseMove={onMove} onMouseLeave={() => setHi(null)} className="block">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        {gridVals.map((g, i) => (
          <g key={i}>
            <line x1={padX} x2={w - padX} y1={y(g)} y2={y(g)} stroke="#ffffff14" strokeDasharray="2 4" />
            <text x={padX} y={y(g) - 4} className="fill-bone-600 font-mono" fontSize={9}>
              {Math.round(g).toLocaleString()}
            </text>
          </g>
        ))}
        <path d={area} fill={`url(#${gid})`} />
        <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hi !== null && (
          <g>
            <line x1={x(hi)} x2={x(hi)} y1={padTop} y2={padTop + plotH} stroke="#ffffff33" />
            <circle cx={x(hi)} cy={y(data[hi])} r={4.5} fill={color} stroke="#20202a" strokeWidth={2} />
          </g>
        )}
        {/* axis end labels */}
        <text x={padX} y={height - 6} className="fill-bone-600 font-mono" fontSize={9}>
          {n - 1}d ago
        </text>
        <text x={w - padX} y={height - 6} textAnchor="end" className="fill-bone-600 font-mono" fontSize={9}>
          now
        </text>
      </svg>

      {hi !== null && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg border border-white/10 bg-ink-900/90 px-2.5 py-1.5 backdrop-blur"
          style={{ left: Math.max(48, Math.min(w - 48, x(hi))), top: 2 }}
        >
          <div className="font-mono text-sm font-semibold tabular-nums" style={{ color }}>
            {data[hi].toLocaleString()}
          </div>
          <div className="font-mono text-[10px] text-bone-500">{hi === n - 1 ? "now" : `${n - 1 - hi}d ago`}</div>
        </div>
      )}
    </div>
  );
}
