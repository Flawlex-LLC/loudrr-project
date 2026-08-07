"use client";

import { CountUp } from "./CountUp";
import { SCORE_MAX, tierFor } from "@/lib/score";

// Circular score gauge: a single arc fills to score/SCORE_MAX in the tier color.
// Score counts up in the center. Flat — no glow, no gradient.
export function SignalMeter({ score, size = 200 }: { score: number; size?: number }) {
  const tier = tierFor(score);
  const stroke = Math.max(9, Math.round(size * 0.05));
  const r = (size - stroke) / 2 - 5;
  const labelSize = Math.max(9, Math.round(size * 0.045));
  const c = 2 * Math.PI * r;
  const pct = Math.min(1, score / SCORE_MAX);
  const dash = c * pct;

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#34343f" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tier.color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
          style={{
            transition: "stroke-dasharray 1.1s cubic-bezier(0.16,1,0.3,1)",
            // hybrid: one deliberate signature glow on the hero ring (subtle, ~25% alpha)
            filter: `drop-shadow(0 0 10px ${tier.color}40)`,
          }}
        />
      </svg>

      <div className="absolute inset-0 grid place-content-center text-center">
        <div className="font-mono uppercase tracking-[0.28em] text-bone-500" style={{ fontSize: labelSize }}>
          Loudrr Score
        </div>
        <div
          className="font-mono font-bold leading-none tracking-tight"
          style={{ color: tier.color, fontSize: Math.round(size * 0.2) }}
        >
          <CountUp to={score} />
        </div>
        <div className="mt-0.5 font-mono text-bone-500" style={{ fontSize: labelSize }}>
          / {SCORE_MAX.toLocaleString()}
        </div>
      </div>
    </div>
  );
}
