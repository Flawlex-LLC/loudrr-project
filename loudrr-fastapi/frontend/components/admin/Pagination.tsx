'use client';

import { ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { num } from '@/lib/admin/format';

interface PaginationProps {
  /** 1-based current page. */
  page: number;
  pageSize: number;
  /** Total rows across all pages (not the length of the current page). */
  total: number;
  onPageChange: (page: number) => void;
  className?: string;
}

/**
 * Page selector with a "Showing X–Y of N" readout.
 *
 * Keeps the number strip to at most 5 buttons (first / last always reachable,
 * ellipses in between) so it fits a 390px-wide phone without wrapping.
 */
export function Pagination({ page, pageSize, total, onPageChange, className }: PaginationProps) {
  const safeSize = Math.max(1, pageSize);
  const pageCount = Math.max(1, Math.ceil(Math.max(0, total) / safeSize));
  const current = Math.min(Math.max(1, page), pageCount);

  const from = total === 0 ? 0 : (current - 1) * safeSize + 1;
  const to = Math.min(current * safeSize, total);

  const go = (p: number) => {
    const next = Math.min(Math.max(1, p), pageCount);
    if (next !== current) onPageChange(next);
  };

  return (
    <div
      className={cn(
        'flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] px-1 py-3',
        className,
      )}
    >
      <p className="text-xs text-zinc-400">
        {total === 0 ? (
          'No results'
        ) : (
          <>
            Showing <span className="font-medium text-zinc-200 tabular-nums">{num(from)}</span>–
            <span className="font-medium text-zinc-200 tabular-nums">{num(to)}</span> of{' '}
            <span className="font-medium text-zinc-200 tabular-nums">{num(total)}</span>
          </>
        )}
      </p>

      {pageCount > 1 && (
        <nav aria-label="Pagination" className="flex items-center gap-1">
          <PageButton
            onClick={() => go(current - 1)}
            disabled={current <= 1}
            ariaLabel="Previous page"
          >
            <ChevronLeft size={14} aria-hidden />
          </PageButton>

          {pageWindow(current, pageCount).map((p, i) =>
            p === null ? (
              <span key={`gap-${i}`} className="px-1 text-xs text-zinc-500" aria-hidden>
                …
              </span>
            ) : (
              <PageButton
                key={p}
                onClick={() => go(p)}
                active={p === current}
                ariaLabel={`Page ${p}`}
                ariaCurrent={p === current}
              >
                <span className="tabular-nums">{p}</span>
              </PageButton>
            ),
          )}

          <PageButton
            onClick={() => go(current + 1)}
            disabled={current >= pageCount}
            ariaLabel="Next page"
          >
            <ChevronRight size={14} aria-hidden />
          </PageButton>
        </nav>
      )}
    </div>
  );
}

/** `null` entries render as an ellipsis. Always shows first + last page. */
function pageWindow(current: number, pageCount: number): Array<number | null> {
  if (pageCount <= 5) return Array.from({ length: pageCount }, (_, i) => i + 1);

  const out: Array<number | null> = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(pageCount - 1, current + 1);

  if (start > 2) out.push(null);
  for (let p = start; p <= end; p++) out.push(p);
  if (end < pageCount - 1) out.push(null);
  out.push(pageCount);
  return out;
}

function PageButton({
  children,
  onClick,
  disabled,
  active,
  ariaLabel,
  ariaCurrent,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  ariaLabel: string;
  ariaCurrent?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-current={ariaCurrent ? 'page' : undefined}
      className={cn(
        'inline-flex h-8 min-w-[32px] items-center justify-center rounded-md border px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f95400]/40',
        active
          ? 'border-[#f95400]/40 bg-[#f95400]/15 text-white'
          : 'border-white/[0.08] bg-white/[0.03] text-zinc-400 hover:border-white/[0.16] hover:bg-white/[0.07] hover:text-white',
        'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-white/[0.03] disabled:hover:text-zinc-400',
      )}
    >
      {children}
    </button>
  );
}
