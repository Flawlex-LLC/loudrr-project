'use client';

import { cn } from '@/lib/utils';

type Tone = 'neutral' | 'admin' | 'superadmin' | 'success' | 'danger' | 'warning' | 'info';

const TONES: Record<Tone, string> = {
  neutral: 'bg-white/[0.06] text-zinc-400 border-white/[0.08]',
  admin: 'bg-blue-950/60 text-blue-300 border-blue-900/60',
  superadmin: 'bg-purple-950/60 text-purple-300 border-purple-900/60',
  success: 'bg-emerald-950/60 text-emerald-300 border-emerald-900/60',
  danger: 'bg-red-950/60 text-red-300 border-red-900/60',
  warning: 'bg-amber-950/60 text-amber-300 border-amber-900/60',
  info: 'bg-cyan-950/60 text-cyan-300 border-cyan-900/60',
};

export function Badge({ children, tone = 'neutral', className }: { children: React.ReactNode; tone?: Tone; className?: string }) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
      TONES[tone],
      className,
    )}>
      {children}
    </span>
  );
}
