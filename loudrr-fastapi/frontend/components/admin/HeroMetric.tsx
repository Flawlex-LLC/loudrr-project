'use client';

import { motion } from 'framer-motion';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

interface HeroMetricProps {
  label: string;
  value: string | number;
  delta?: number | null;
  hint?: string;
  icon?: LucideIcon;
}

function formatDelta(d: number): string {
  const abs = Math.abs(d);
  const fixed = abs >= 100 ? abs.toFixed(0) : abs.toFixed(1);
  return `${fixed}%`;
}

export function HeroMetric({ label, value, delta, hint, icon: Icon }: HeroMetricProps) {
  let deltaNode: React.ReactNode = null;
  if (delta === null || delta === undefined) {
    deltaNode = (
      <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[11px] font-medium text-zinc-500">
        <Minus size={11} />
        no prior data
      </span>
    );
  } else if (delta > 0) {
    deltaNode = (
      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-900/50 bg-emerald-950/40 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
        <ArrowUpRight size={11} />
        {formatDelta(delta)}
      </span>
    );
  } else if (delta < 0) {
    deltaNode = (
      <span className="inline-flex items-center gap-1 rounded-full border border-red-900/50 bg-red-950/40 px-2 py-0.5 text-[11px] font-semibold text-red-300">
        <ArrowDownRight size={11} />
        {formatDelta(delta)}
      </span>
    );
  } else {
    deltaNode = (
      <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[11px] font-medium text-zinc-400">
        <Minus size={11} />
        0%
      </span>
    );
  }

  return (
    <motion.div
      whileHover={{ scale: 1.005 }}
      transition={{ type: 'spring', stiffness: 400, damping: 30 }}
      className={cn(
        'glass-card group relative overflow-hidden p-6',
        'hover:border-[rgba(249,84,0,0.35)]'
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</div>
        </div>
        {Icon && (
          <div className="glass-icon glass-icon-orange glass-icon-md shrink-0">
            <Icon size={18} className="text-[#f95400]" />
          </div>
        )}
      </div>

      <div className="mt-3 flex items-baseline gap-3">
        <div className="stat-glow text-4xl font-bold leading-none tracking-tight text-white tabular-nums">
          {value}
        </div>
        {deltaNode}
      </div>

      {hint && <div className="mt-2 text-xs text-zinc-500">{hint}</div>}
    </motion.div>
  );
}
