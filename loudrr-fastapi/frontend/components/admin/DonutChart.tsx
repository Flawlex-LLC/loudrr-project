'use client';

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import type { ContentType } from 'recharts/types/component/Tooltip';
import type { NameType, ValueType } from 'recharts/types/component/DefaultTooltipContent';

export interface DonutDatum {
  name: string;
  value: number;
  color?: string;
}

interface DonutChartProps {
  data: DonutDatum[];
  total: number;
  label: string;
  height?: number;
}

const DEFAULT_PALETTE = [
  '#f95400', // orange
  '#3f3f46', // zinc-700
  '#ff8c42', // light orange
  '#52525b', // zinc-600
  '#cc5500', // deep orange
  '#71717a', // zinc-500
];

export function DonutChart({ data, total, label, height = 200 }: DonutChartProps) {
  const colored = data.map((d, i) => ({
    ...d,
    color: d.color ?? DEFAULT_PALETTE[i % DEFAULT_PALETTE.length],
  }));

  const renderTooltip: ContentType<ValueType, NameType> = ({ active, payload }) => {
    if (!active || !payload || payload.length === 0) return null;
    const entry = payload[0];
    const raw = entry?.value;
    const num =
      typeof raw === 'number' ? raw : Array.isArray(raw) ? Number(raw[0] ?? 0) : Number(raw ?? 0);
    const safeNum = Number.isFinite(num) ? num : 0;
    const payloadObj = entry?.payload as (DonutDatum & { fill?: string }) | undefined;
    const name = String(entry?.name ?? payloadObj?.name ?? '');
    const color = payloadObj?.color ?? payloadObj?.fill ?? '#f95400';
    return (
      <div className="rounded-md border border-white/[0.08] bg-[#111] px-2.5 py-1.5 text-xs shadow-xl shadow-black/40">
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: color }}
          />
          <span className="text-zinc-300">{name}</span>
        </div>
        <div className="mt-0.5 text-sm font-bold text-white tabular-nums">
          {safeNum.toLocaleString()}
        </div>
      </div>
    );
  };

  return (
    <div className="relative w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={colored}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius="60%"
            outerRadius="90%"
            paddingAngle={1}
            stroke="none"
            isAnimationActive={false}
          >
            {colored.map((entry, i) => (
              <Cell key={`cell-${i}`} fill={entry.color} />
            ))}
          </Pie>
          <Tooltip content={renderTooltip} />
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <div className="stat-glow text-2xl font-bold leading-none tabular-nums text-white">
          {total.toLocaleString()}
        </div>
        <div className="mt-1 text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
      </div>
    </div>
  );
}
