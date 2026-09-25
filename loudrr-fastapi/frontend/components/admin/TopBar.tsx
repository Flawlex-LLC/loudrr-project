'use client';

import { ChevronRight, ExternalLink, Menu, LogOut } from 'lucide-react';

interface TopBarProps {
  breadcrumb: string[];
  /** Opens the mobile nav drawer. Rendered under `lg` only. */
  onMenuClick?: () => void;
  /** Drawer state, for `aria-expanded` on the hamburger. */
  menuOpen?: boolean;
  /** Ends the admin website session. */
  onSignOut?: () => void;
}

/**
 * The API-docs link used to be a hardcoded `http://localhost:8000/docs`, as
 * was a second link to SQLAdmin. In production that resolves to the ADMIN'S
 * OWN machine — on a phone inside Telegram it points at the handset. SQLAdmin
 * is being retired, so it's gone; the docs link only renders outside
 * production, where localhost is genuinely the backend.
 */
const IS_PRODUCTION = process.env.NODE_ENV === 'production';
const DOCS_URL = `${(process.env.NEXT_PUBLIC_ADMIN_API_URL || 'http://localhost:8000/api/admin').replace(
  /\/api\/admin\/?$/,
  '',
)}/docs`;

export function TopBar({ breadcrumb, onMenuClick, menuOpen = false, onSignOut }: TopBarProps) {
  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-white/[0.06] bg-[#0a0a0a]/85 px-4 backdrop-blur lg:gap-4 lg:px-6">
      {/* Hamburger — the only way to reach navigation under `lg`. */}
      <button
        type="button"
        onClick={onMenuClick}
        aria-label="Open navigation"
        aria-expanded={menuOpen}
        className="-ml-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.02] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f95400]/40 lg:hidden"
      >
        <Menu size={18} />
      </button>

      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 text-xs">
        {breadcrumb.map((crumb, i) => {
          const isLast = i === breadcrumb.length - 1;
          return (
            <span key={`${crumb}-${i}`} className="flex min-w-0 items-center gap-1">
              {/* Under `sm` only the final crumb survives, so its separator
                  goes too — otherwise the bar opens with a stray chevron. */}
              {i > 0 && (
                <ChevronRight size={12} className="hidden shrink-0 text-zinc-600 sm:inline" aria-hidden />
              )}
              <span
                className={
                  isLast ? 'truncate font-medium text-white' : 'hidden truncate text-zinc-400 sm:inline'
                }
              >
                {crumb}
              </span>
            </span>
          );
        })}
      </nav>

      {/* Right side: dev-only external link + audit hint */}
      <div className="ml-auto flex items-center gap-3 text-xs text-zinc-400">
        {!IS_PRODUCTION && (
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="hidden items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 transition-all duration-300 hover:border-white/20 hover:bg-white/[0.05] hover:text-white active:scale-[0.98] sm:inline-flex"
            title="FastAPI Swagger docs (local dev only)"
          >
            <span>API docs</span>
            <ExternalLink size={11} />
          </a>
        )}
        <div className="hidden text-[11px] text-zinc-500 md:block">All actions audit-logged</div>
        {onSignOut && (
          <button
            type="button"
            onClick={onSignOut}
            className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-1.5 text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.05] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f95400]/40"
          >
            <LogOut size={12} aria-hidden />
            <span>Sign out</span>
          </button>
        )}
      </div>
    </header>
  );
}
