'use client';

import {
  Area,
  AreaChart as RechartsAreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { ContentType } from 'recharts/types/component/Tooltip';
import type { NameType, ValueType } from 'recharts/types/component/DefaultTooltipContent';

export interface AreaChartDatum {
  date: string;
  value: number;
}

interface AreaChartProps {
  data: AreaChartDatum[];
  color?: string;
  height?: number;
  valueFormatter?: (n: number) => string;
}

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatShortDate(input: string): string {
  const d = new Date(input);
  if (Number.isNaN(d.getTime())) return input;
  return `${MONTHS_SHORT[d.getMonth()]} ${d.getDate()}`;
}

export function AreaChart({
  data,
  color = '#f95400',
  height = 280,
  valueFormatter,
}: AreaChartProps) {
  const fmt = valueFormatter ?? ((n: number) => n.toLocaleString());

  const renderTooltip: ContentType<ValueType, NameType> = ({ active, payload, label }) => {
    if (!active || !payload || payload.length === 0) return null;
    const raw = payload[0]?.value;
    const num =
      typeof raw === 'number' ? raw : Array.isArray(raw) ? Number(raw[0] ?? 0) : Number(raw ?? 0);
    const safeNum = Number.isFinite(num) ? num : 0;
    return (
      <div className="rounded-md border border-white/[0.08] bg-[#111] px-2.5 py-1.5 text-xs shadow-xl shadow-black/40">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500">
          {typeof label === 'string' ? formatShortDate(label) : String(label ?? '')}
        </div>
        <div className="mt-0.5 text-sm font-bold text-white tabular-nums">{fmt(safeNum)}</div>
      </div>
    );
  };

  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsAreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="orangeFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.4} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="transparent" vertical={false} horizontal={false} />
        <XAxis
          dataKey="date"
          tickLine={false}
          axisLine={false}
          tick={{ fill: '#71717a', fontSize: 11 }}
          tickFormatter={formatShortDate}
          minTickGap={24}
        />
        <YAxis hide axisLine={false} tickLine={false} />
        <Tooltip content={renderTooltip} cursor={{ stroke: 'rgba(249,84,0,0.25)', strokeWidth: 1 }} />
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          fill="url(#orangeFill)"
          dot={false}
          activeDot={{ r: 4, fill: color, stroke: '#0a0a0a', strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </RechartsAreaChart>
    </ResponsiveContainer>
  );
}
