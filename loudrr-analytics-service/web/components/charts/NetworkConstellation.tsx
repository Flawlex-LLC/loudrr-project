"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { type Receipt } from "@/lib/mock";
import { tierFor } from "@/lib/score";

// Radial node-link graph of the account's elite voters. Higher-scoring voters sit closer to the
// center; edges carry a "signal pulse" travelling outward (the Loudrr motif). Deterministic
// layout (golden-angle), element-level hover — no width measurement, no d3.
export function NetworkConstellation({
  centerHandle,
  centerScore,
  nodes,
}: {
  centerHandle: string;
  centerScore: number;
  nodes: Receipt[];
}) {
  const router = useRouter();
  const [hover, setHover] = useState<string | null>(null);
  const W = 600;
  const H = 440;
  const cx = W / 2;
  const cy = H / 2;
  const centerColor = tierFor(centerScore).color;

  const placed = nodes.slice(0, 14).map((nd, i) => {
    const ang = i * 2.399963; // golden angle
    const sNorm = Math.min(1, nd.score / 6000);
    const radius = 78 + (1 - sNorm) * 150 + (i % 3) * 10;
    const x = cx + Math.cos(ang) * radius * 1.22;
    const y = cy + Math.sin(ang) * radius * 0.92;
    const r = 7 + sNorm * 13;
    return { ...nd, x: clampN(x, r + 6, W - r - 6), y: clampN(y, r + 6, H - r - 6), r, color: tierFor(nd.score).color };
  });

  if (placed.length === 0) {
    return (
      <div className="grid place-items-center py-16 text-center font-mono text-xs text-bone-500">
        No smart followers scored yet for @{centerHandle}.
      </div>
    );
  }

  return (
    // decorative — the role-tabbed smart-followers grid above is the accessible equivalent
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ aspectRatio: `${W}/${H}` }} role="img" aria-label={`Influence network: ${placed.length} smart followers around @${centerHandle}`}>
      {/* edges + travelling signal pulses */}
      {placed.map((p, i) => (
        <g key={`e-${p.handle}`}>
          <line x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="#ffffff" strokeOpacity={hover === p.handle ? 0.35 : 0.1} strokeWidth={1} />
          <circle r={2} fill={centerColor} opacity={0}>
            <animate attributeName="cx" values={`${cx};${p.x}`} dur="2.6s" begin={`${(i % 7) * 0.4}s`} repeatCount="indefinite" />
            <animate attributeName="cy" values={`${cy};${p.y}`} dur="2.6s" begin={`${(i % 7) * 0.4}s`} repeatCount="indefinite" />
            <animate attributeName="opacity" values="0;0.9;0" dur="2.6s" begin={`${(i % 7) * 0.4}s`} repeatCount="indefinite" />
          </circle>
        </g>
      ))}

      {/* neighbour nodes */}
      {placed.map((p) => (
        <g
          key={p.handle}
          className="cursor-pointer"
          onMouseEnter={() => setHover(p.handle)}
          onMouseLeave={() => setHover(null)}
          onClick={() => router.push(`/score/${p.handle}`)}
        >
          <circle cx={p.x} cy={p.y} r={p.r} fill={p.color} fillOpacity={hover === p.handle ? 0.4 : 0.16} stroke={p.color} strokeWidth={1.5} />
          <text
            x={p.x}
            y={p.y + p.r + 12}
            textAnchor="middle"
            fontSize={10}
            className="fill-bone-300 font-mono transition-opacity"
            style={{ opacity: hover === p.handle ? 1 : 0.55 }}
          >
            @{p.handle.length > 12 ? p.handle.slice(0, 11) + "…" : p.handle}
          </text>
        </g>
      ))}

      {/* center node */}
      <g>
        {[34, 50, 68].map((r, i) => (
          <circle key={r} cx={cx} cy={cy} r={r} fill="none" stroke={centerColor} strokeOpacity={0.12 - i * 0.03}>
            <animate attributeName="r" values={`${r};${r + 16}`} dur="3s" begin={`${i * 0.6}s`} repeatCount="indefinite" />
            <animate attributeName="stroke-opacity" values={`${0.18 - i * 0.04};0`} dur="3s" begin={`${i * 0.6}s`} repeatCount="indefinite" />
          </circle>
        ))}
        <circle cx={cx} cy={cy} r={26} fill={centerColor} fillOpacity={0.22} stroke={centerColor} strokeWidth={2} />
        <text x={cx} y={cy + 44} textAnchor="middle" fontSize={11} className="fill-bone-100 font-mono font-semibold">
          @{centerHandle}
        </text>
      </g>
    </svg>
  );
}

function clampN(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}
