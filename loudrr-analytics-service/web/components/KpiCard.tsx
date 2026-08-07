"use client";

import { CountUp } from "./CountUp";
import { DeltaPair } from "./DeltaPair";
import { Sparkline } from "./charts/Sparkline";
import { fmtCompact } from "@/lib/score";
import type { Delta } from "@/lib/mock";

// Frosted KPI tile: label, hero figure (count-up for plain numbers), optional dual delta and a
// faint background sparkline. The top-of-page stat row on the score dashboard.
export function KpiCard({
  label,
  value,
  format = "num",
  delta,
  deltaUnit,
  spark,
  sparkColor = "#ff6a00",
}: {
  label: string;
  value: number;
  format?: "num" | "compact" | "pct" | "ratio";
  delta?: Delta;
  deltaUnit?: string;
  spark?: number[];
  sparkColor?: string;
  accent?: boolean;
}) {
  const display =
    format === "compact"
      ? fmtCompact(value)
      : format === "pct"
        ? `${value.toFixed(value < 1 ? 2 : 1)}%`
        : format === "ratio"
          ? `${(value * 100).toFixed(1)}%`
          : null;

  return (
    <div className="glass relative overflow-hidden p-4">
      {spark && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 opacity-30">
          <Sparkline data={spark} width={300} height={40} color={sparkColor} dot={false} strokeWidth={1.25} />
        </div>
      )}
      <div className="relative">
        <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-bone-500">{label}</div>
        <div
          className="mt-1.5 font-mono text-3xl font-bold tabular-nums text-bone-100"
        >
          {display ?? <CountUp to={value} />}
        </div>
        {delta && (
          <div className="mt-1.5 flex items-center gap-1.5">
            <DeltaPair delta={delta} size="sm" />
            {deltaUnit && <span className="font-mono text-[10px] text-bone-600">{deltaUnit}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
