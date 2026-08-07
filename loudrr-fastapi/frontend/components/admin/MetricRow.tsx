'use client';

import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

type Tone = 'success' | 'danger' | 'warning' | 'default';

interface MetricRowProps {
  label: string;
  value: string | number;
  tone?: Tone;
  icon?: LucideIcon;
}

const TONE_VALUE: Record<Tone, string> = {
  default: 'text-white',
  success: 'text-emerald-300',
  danger: 'text-red-300',
  warning: 'text-amber-300',
};

const TONE_ICON: Record<Tone, string> = {
  default: 'text-zinc-500',
  success: 'text-emerald-400',
  danger: 'text-red-400',
  warning: 'text-amber-400',
};

export function MetricRow({ label, value, tone = 'default', icon: Icon }: MetricRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-white/[0.03]">
      <div className="flex min-w-0 items-center gap-2">
        {Icon && <Icon size={13} className={cn('shrink-0', TONE_ICON[tone])} />}
        <span className="truncate text-xs text-zinc-500">{label}</span>
      </div>
      <span className={cn('text-sm font-semibold tabular-nums', TONE_VALUE[tone])}>
        {value}
      </span>
    </div>
  );
}
