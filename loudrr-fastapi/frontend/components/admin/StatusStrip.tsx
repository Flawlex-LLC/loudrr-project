'use client';

import { cn } from '@/lib/utils';

export type StatusTone = 'ok' | 'warn' | 'bad';

export interface StatusStripItem {
  label: string;
  value: string;
  tone?: StatusTone;
  /** Longer explanation — shown as a native tooltip on the cell. */
  hint?: string;
}

interface StatusStripProps {
  items: StatusStripItem[];
  className?: string;
}

const DOT: Record<StatusTone, string> = {
  ok: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
  warn: 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]',
  bad: 'bg-red-400 shadow-[0_0_8px_rgba(248,113,113,0.6)]',
};

const VALUE: Record<StatusTone, string> = {
  ok: 'text-emerald-300',
  warn: 'text-amber-300',
  bad: 'text-red-300',
};

/**
 * A compact row of labelled readouts for the top of a page — queue depth,
 * last run, provider health.
 *
 * Wraps to a two-column grid under `sm` so it stays readable at 390px instead
 * of squeezing five cells onto one line.
 */
export function StatusStrip({ items, className }: StatusStripProps) {
  if (items.length === 0) return null;

  return (
    <dl
      className={cn(
        'grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-white/[0.06] bg-white/[0.06] sm:grid-cols-3 lg:grid-cols-[repeat(auto-fit,minmax(150px,1fr))]',
        className,
      )}
    >
      {items.map((item) => {
        const tone = item.tone;
        return (
          <div
            key={item.label}
            className="flex min-w-0 flex-col gap-1 bg-[#0d0d0d] px-3 py-2.5"
            title={item.hint}
          >
            <dt className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-zinc-400">
              {tone && <span aria-hidden className={cn('h-1.5 w-1.5 shrink-0 rounded-full', DOT[tone])} />}
              <span className="truncate">{item.label}</span>
            </dt>
            <dd
              className={cn(
                'truncate text-sm font-semibold tabular-nums',
                tone ? VALUE[tone] : 'text-white',
              )}
            >
              {item.value}
            </dd>
            {item.hint && <span className="sr-only">{item.hint}</span>}
          </div>
        );
      })}
    </dl>
  );
}
