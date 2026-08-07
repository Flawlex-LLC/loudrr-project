"use client";

import { useEffect, useRef, useState } from "react";
import { fmtCompact } from "@/lib/score";

export type StatSeries = {
  key: string;
  label: string;
  color: string;
  data: number[];
  axis: "left" | "right";
  format?: (v: number) => string;
};

export type StatMarker = { index: number; img: string; ring: string; name: string };

// Dual-axis multi-line chart (TwitterScore-style): smart followers on the left axis, Loudrr Score
// on the right. Bitget-style: each new smart follower's pfp is plotted on the followers line at the
// day they followed. Crosshair tooltip shows every visible series; the legend toggles them.
export function StatsChart({
  series,
  markers = [],
  height = 240,
}: {
  series: StatSeries[];
  markers?: StatMarker[];
  height?: number;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(720);
  const [hi, setHi] = useState<number | null>(null);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver((es) => setW(es[0].contentRect.width));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const n = series[0]?.data.length ?? 0;
  if (n < 2) return <div ref={wrap} style={{ height }} />;

  const padX = 30;
  const padTop = 12;
  const padBottom = 22;
  const plotH = height - padTop - padBottom;

  const visible = series.filter((s) => !hidden[s.key]);

  const domain = (axis: "left" | "right") => {
    const ds = visible.filter((s) => s.axis === axis).flatMap((s) => s.data);
    if (!ds.length) return null;
    const lo = Math.min(...ds);
    const hiV = Math.max(...ds);
    const pad = (hiV - lo) * 0.14 || Math.max(1, hiV * 0.05);
    return { lo: lo - pad, hi: hiV + pad };
  };
  const left = domain("left");
  const right = domain("right");

  const x = (i: number) => padX + (i / (n - 1)) * (w - padX * 2);
  const y = (v: number, axis: "left" | "right") => {
    const d = axis === "left" ? left : right;
    if (!d) return padTop + plotH;
    const span = d.hi - d.lo || 1;
    return padTop + plotH - ((v - d.lo) / span) * plotH;
  };

  const linePath = (s: StatSeries) =>
    s.data.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v, s.axis).toFixed(1)}`).join(" ");

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = e.clientX - rect.left - padX;
    const idx = Math.round((rel / (w - padX * 2)) * (n - 1));
    setHi(Math.max(0, Math.min(n - 1, idx)));
  };

  const primary = visible.find((s) => s.axis === "left") ?? visible[0];
  const sfSeries = series.find((s) => s.axis === "left");

  return (
    <div>
      <div ref={wrap} className="relative select-none" style={{ height }}>
        <svg width={w} height={height} onMouseMove={onMove} onMouseLeave={() => setHi(null)} className="block">
          <defs>
            <linearGradient id="stats-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={primary?.color ?? "#f95400"} stopOpacity={0.2} />
              <stop offset="100%" stopColor={primary?.color ?? "#f95400"} stopOpacity={0} />
            </linearGradient>
          </defs>

          {[0, 0.5, 1].map((g) => {
            const yy = padTop + plotH * g;
            return <line key={g} x1={padX} x2={w - padX} y1={yy} y2={yy} stroke="#ffffff12" strokeDasharray="2 4" />;
          })}

          {/* left-axis ticks (primary / smart followers) */}
          {left &&
            [1, 0.5, 0].map((g, i) => (
              <text key={`l${i}`} x={2} y={padTop + plotH * (1 - g) + 3} fontSize={9} className="fill-bone-600 font-mono">
                {fmtCompact(left.lo + (left.hi - left.lo) * g)}
              </text>
            ))}
          {/* right-axis ticks (score) */}
          {right &&
            [1, 0.5, 0].map((g, i) => (
              <text
                key={`r${i}`}
                x={w - 2}
                y={padTop + plotH * (1 - g) + 3}
                textAnchor="end"
                fontSize={9}
                className="fill-bone-600 font-mono"
              >
                {Math.round(right.lo + (right.hi - right.lo) * g).toLocaleString()}
              </text>
            ))}

          {primary && (
            <path
              d={`${linePath(primary)} L${x(n - 1).toFixed(1)} ${padTop + plotH} L${x(0).toFixed(1)} ${padTop + plotH} Z`}
              fill="url(#stats-area)"
            />
          )}
          {visible.map((s) => (
            <path
              key={s.key}
              d={linePath(s)}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}

          {/* each new smart follower's pfp on the followers line, at the day they followed (Bitget-style) */}
          {sfSeries &&
            !hidden[sfSeries.key] &&
            markers.map((m, j) => {
              const cx = x(m.index);
              const cy = y(sfSeries.data[m.index], "left");
              const r = 9;
              return (
                <g key={`m${j}`}>
                  <clipPath id={`sfav${j}`}>
                    <circle cx={cx} cy={cy} r={r} />
                  </clipPath>
                  <circle cx={cx} cy={cy} r={r + 1.5} fill="#0a0a0a" stroke={m.ring} strokeWidth={2} />
                  <image
                    href={m.img}
                    x={cx - r}
                    y={cy - r}
                    width={r * 2}
                    height={r * 2}
                    clipPath={`url(#sfav${j})`}
                    preserveAspectRatio="xMidYMid slice"
                  />
                </g>
              );
            })}

          {hi !== null && (
            <g>
              <line x1={x(hi)} x2={x(hi)} y1={padTop} y2={padTop + plotH} stroke="#ffffff33" />
              {visible.map((s) => (
                <circle key={s.key} cx={x(hi)} cy={y(s.data[hi], s.axis)} r={4} fill={s.color} stroke="#0a0a0a" strokeWidth={2} />
              ))}
            </g>
          )}

          <text x={padX} y={height - 5} className="fill-bone-600 font-mono" fontSize={9}>
            {n - 1}d ago
          </text>
          <text x={w - padX} y={height - 5} textAnchor="end" className="fill-bone-600 font-mono" fontSize={9}>
            now
          </text>
        </svg>

        {hi !== null && (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg border border-white/10 bg-ink-900/95 px-2.5 py-1.5 backdrop-blur"
            style={{ left: Math.max(70, Math.min(w - 70, x(hi))), top: 2 }}
          >
            <div className="font-mono text-[10px] text-bone-500">{hi === n - 1 ? "now" : `${n - 1 - hi}d ago`}</div>
            {visible.map((s) => (
              <div key={s.key} className="mt-0.5 flex items-center gap-2 font-mono text-xs">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: s.color }} />
                <span className="text-bone-400">{s.label}</span>
                <span className="ml-3 font-semibold tabular-nums" style={{ color: s.color }}>
                  {s.format ? s.format(s.data[hi]) : s.data[hi].toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* legend toggles — both on by default */}
      <div className="mt-3 flex flex-wrap items-center justify-center gap-5">
        {series.map((s) => {
          const on = !hidden[s.key];
          return (
            <button
              key={s.key}
              onClick={() => setHidden((h) => ({ ...h, [s.key]: on }))}
              className="flex items-center gap-2 text-xs transition-opacity"
              aria-pressed={on}
            >
              <span
                className="grid h-4 w-4 place-items-center rounded-[5px] border"
                style={{ borderColor: s.color, background: on ? s.color : "transparent" }}
              >
                {on && (
                  <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="#0a0a0a" strokeWidth={3}>
                    <path d="M5 12l5 5L20 6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
              <span className={on ? "text-bone-200" : "text-bone-500"}>{s.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
