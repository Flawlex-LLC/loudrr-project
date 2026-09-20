'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

interface TableScrollProps {
  /**
   * Minimum width (px) the content needs before it may wrap. Below this the
   * container scrolls horizontally instead of crushing the columns — which is
   * what used to clip every table's action buttons off-screen.
   */
  minWidth?: number;
  children: React.ReactNode;
  className?: string;
}

/**
 * Horizontal scroll container for admin tables.
 *
 * Wrap a `<table>` in this and give it the width it actually needs. A fading
 * edge appears on whichever side has more content, so it's obvious there are
 * columns (usually the action buttons) out of view — the previous silent clip
 * was invisible on a phone AND at 1280/1440 on wide tables.
 *
 *   <TableScroll minWidth={960}>
 *     <table className="w-full min-w-full">…</table>
 *   </TableScroll>
 */
export function TableScroll({ minWidth, children, className }: TableScrollProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    // 1px slack: sub-pixel layout rounding otherwise leaves a permanent fade.
    setEdges({ left: el.scrollLeft > 1, right: el.scrollLeft < max - 1 });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    measure();
    // ResizeObserver covers both the viewport changing and rows arriving.
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [measure]);

  return (
    <div className={cn('relative', className)}>
      <div
        ref={scrollRef}
        onScroll={measure}
        className="scrollbar-content overflow-x-auto overscroll-x-contain"
      >
        <div style={minWidth ? { minWidth } : undefined}>{children}</div>
      </div>

      {/* Edge fades — pointer-events-none so they never eat a click on the
          cell underneath. */}
      <div
        aria-hidden
        className={cn(
          'pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-[#0a0a0a] to-transparent transition-opacity duration-200',
          edges.left ? 'opacity-90' : 'opacity-0',
        )}
      />
      <div
        aria-hidden
        className={cn(
          'pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-[#0a0a0a] to-transparent transition-opacity duration-200',
          edges.right ? 'opacity-90' : 'opacity-0',
        )}
      />
    </div>
  );
}
