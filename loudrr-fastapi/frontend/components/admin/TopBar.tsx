'use client';

import { ChevronRight, ExternalLink } from 'lucide-react';

interface TopBarProps {
  breadcrumb: string[];
}

export function TopBar({ breadcrumb }: TopBarProps) {
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-white/[0.06] bg-[#0a0a0a]/85 px-6 backdrop-blur">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 text-xs">
        {breadcrumb.map((crumb, i) => {
          const isLast = i === breadcrumb.length - 1;
          return (
            <span key={`${crumb}-${i}`} className="flex items-center gap-1">
              {i > 0 && <ChevronRight size={12} className="text-zinc-700" aria-hidden />}
              <span
                className={
                  isLast
                    ? 'truncate font-medium text-white'
                    : 'truncate text-zinc-500'
                }
              >
                {crumb}
              </span>
            </span>
          );
        })}
      </nav>

      {/* Right side: external links + audit hint */}
      <div className="ml-auto flex items-center gap-3 text-xs text-zinc-500">
        <a
          href="http://localhost:8000/docs"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 transition-all duration-300 hover:border-white/20 hover:bg-white/[0.05] hover:text-white active:scale-[0.98]"
          title="FastAPI Swagger docs"
        >
          <span>API docs</span>
          <ExternalLink size={11} />
        </a>
        <a
          href="http://localhost:8000/admin"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 transition-all duration-300 hover:border-white/20 hover:bg-white/[0.05] hover:text-white active:scale-[0.98]"
          title="SQLAdmin raw DB browser"
        >
          <span>SQLAdmin</span>
          <ExternalLink size={11} />
        </a>
        <div className="hidden text-[11px] text-zinc-600 md:block">All actions audit-logged</div>
      </div>
    </header>
  );
}
