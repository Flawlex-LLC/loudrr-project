'use client';

import Link from 'next/link';
import { cn } from '@/lib/utils';
import type { LucideIcon } from 'lucide-react';

interface StatCardProps {
  label: string;
  value: number | string | null;
  hint?: string;
  href?: string;
  icon?: LucideIcon;
  tone?: 'default' | 'attention' | 'success' | 'danger';
}

const TONES: Record<NonNullable<StatCardProps['tone']>, { ring: string; iconBg: string; iconColor: string }> = {
  default: { ring: 'border-white/[0.08]', iconBg: 'bg-white/[0.04]', iconColor: 'text-zinc-400' },
  attention: { ring: 'border-amber-900/50', iconBg: 'bg-amber-950/40', iconColor: 'text-amber-400' },
  success: { ring: 'border-emerald-900/50', iconBg: 'bg-emerald-950/40', iconColor: 'text-emerald-400' },
  danger: { ring: 'border-red-900/50', iconBg: 'bg-red-950/40', iconColor: 'text-red-400' },
};

export function StatCard({ label, value, hint, href, icon: Icon, tone = 'default' }: StatCardProps) {
  const t = TONES[tone];
  const content = (
    <div className={cn(
      'h-full rounded-2xl border bg-[#111] p-5 transition-colors',
      t.ring,
      href && 'hover:bg-[#161616] hover:border-white/[0.14]'
    )}>
      <div className="flex items-start justify-between">
        <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</div>
        {Icon && (
          <div className={cn('rounded-md p-1.5', t.iconBg)}>
            <Icon size={14} className={t.iconColor} />
          </div>
        )}
      </div>
      <div className="mt-3 text-3xl font-bold text-white tabular-nums">
        {value === null ? <span className="text-zinc-700">—</span> : value}
      </div>
      {hint && <div className="mt-1 text-xs text-zinc-500">{hint}</div>}
    </div>
  );
  return href ? <Link href={href} className="block">{content}</Link> : content;
}
