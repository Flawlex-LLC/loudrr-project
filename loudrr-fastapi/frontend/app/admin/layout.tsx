'use client';

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { usePathname } from 'next/navigation';
import Link from 'next/link';
import { Toaster } from 'sonner';
import {
  ChevronLeft,
  ChevronRight,
  Crown,
  LayoutDashboard,
  Loader2,
  Megaphone,
  Activity,
  RefreshCw,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
  Users,
} from 'lucide-react';

import { Sidebar, type SidebarItem } from '@/components/admin/Sidebar';
import { TopBar } from '@/components/admin/TopBar';
import { Button } from '@/components/admin/Button';
import {
  AdminSessionProvider,
  badgeCount,
  useAdminSession,
} from '@/lib/admin/session';
import { cn } from '@/lib/utils';

type BadgeKey = 'waitlist' | 'xVerification';

const NAV_BASE: Array<Omit<SidebarItem, 'badge'> & { badgeKey?: BadgeKey }> = [
  { href: '/admin', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/admin/waitlist', label: 'Waitlist', icon: UserCheck, badgeKey: 'waitlist' },
  { href: '/admin/x-verification', label: 'X Verification', icon: ShieldCheck, badgeKey: 'xVerification' },
  { href: '/admin/users', label: 'Users', icon: Users },
  { href: '/admin/sponsors', label: 'Sponsors', icon: Megaphone },
  { href: '/admin/ops', label: 'Operations', icon: Activity },
  { href: '/admin/site-settings', label: 'Site Settings', icon: Settings2 },
];

// ---- Sidebar collapse preference ----
// A tiny external store so the value can be read with useSyncExternalStore.
// Reading localStorage in an effect + setState cost an extra render on every
// mount (and trips react-hooks/set-state-in-effect); reading it during render
// would desync hydration. useSyncExternalStore is the sanctioned middle: the
// server snapshot is always "expanded", and React reconciles after hydration.
const COLLAPSE_KEY = 'loudrr.admin.sidebar.collapsed';
const collapseListeners = new Set<() => void>();
let collapseCache: boolean | null = null;

function getCollapsed(): boolean {
  if (collapseCache === null) {
    try {
      collapseCache = localStorage.getItem(COLLAPSE_KEY) === '1';
    } catch {
      collapseCache = false; // localStorage unavailable (privacy mode)
    }
  }
  return collapseCache;
}

function getCollapsedServerSnapshot(): boolean {
  return false;
}

function subscribeCollapsed(onChange: () => void): () => void {
  collapseListeners.add(onChange);
  return () => {
    collapseListeners.delete(onChange);
  };
}

function writeCollapsed(next: boolean): void {
  collapseCache = next;
  try {
    localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0');
  } catch {
    /* no-op */
  }
  collapseListeners.forEach((l) => l());
}

function breadcrumbFromPathname(pathname: string): string[] {
  // /admin              -> ['Admin', 'Dashboard']
  // /admin/site-settings -> ['Admin', 'Site Settings']
  // /admin/x-verification -> ['Admin', 'X Verification']
  const segments = pathname.split('/').filter(Boolean); // ['admin', ...]
  if (segments.length <= 1) return ['Admin', 'Dashboard'];
  const rest = segments.slice(1).map((seg) =>
    seg
      .split('-')
      .map((word) => (word.toLowerCase() === 'x' ? 'X' : word.charAt(0).toUpperCase() + word.slice(1)))
      .join(' '),
  );
  return ['Admin', ...rest];
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  // The provider owns /me/ and the queue counts; AdminShell consumes them.
  return (
    <AdminSessionProvider>
      <AdminShell>{children}</AdminShell>
    </AdminSessionProvider>
  );
}

function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me, queues, status, error, reload } = useAdminSession();
  const collapsed = useSyncExternalStore(
    subscribeCollapsed,
    getCollapsed,
    getCollapsedServerSnapshot,
  );
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // Sonner takes one position: keep it clear of the TopBar actions on desktop
  // (bottom-right) and out of the thumb zone on a phone (top-center).
  const [toastPosition, setToastPosition] = useState<'bottom-right' | 'top-center'>('bottom-right');

  // Resolved after hydration so SSR and the first client render agree.
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)');
    const apply = () => setToastPosition(mq.matches ? 'bottom-right' : 'top-center');
    apply();
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, []);

  const toggleCollapsed = useCallback(() => {
    writeCollapsed(!getCollapsed());
  }, []);

  const closeMobileNav = useCallback(() => setMobileNavOpen(false), []);

  // Badge counts come from the exact COUNTs in /stats/ (see adminApi.queues),
  // so they no longer cap at a list limit; "99+" keeps the pill from stretching
  // the nav row.
  const navItems: SidebarItem[] = NAV_BASE.map(({ badgeKey, ...item }) => {
    if (badgeKey === 'waitlist') return { ...item, badge: badgeCount(queues.pending_waitlist) };
    if (badgeKey === 'xVerification') {
      return { ...item, badge: badgeCount(queues.pending_x_verifications) };
    }
    return item;
  });

  const breadcrumb = breadcrumbFromPathname(pathname);

  const userDisplayName = me?.telegram_username || (me?.telegram_id ? `@${me.telegram_id}` : '—');
  const userInitial = (userDisplayName[0] || '?').toUpperCase();

  const footer = (
    <div className={cn('flex flex-col gap-2', collapsed && 'items-center')}>
      {/* UserPill — driven by /api/admin/me/ via the session context. */}
      <div
        className={cn(
          'flex items-center gap-2 rounded-md px-2 py-1.5',
          collapsed ? 'justify-center' : 'hover:bg-white/[0.04]',
        )}
        title={collapsed ? `${userDisplayName} (${me?.role || 'admin'})` : undefined}
      >
        <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#f95400] text-xs font-bold text-black">
          {userInitial}
        </div>
        {!collapsed && (
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            <span className="truncate text-xs font-medium text-zinc-200">{userDisplayName}</span>
            {me?.role === 'superadmin' && (
              <span
                className="inline-flex items-center gap-0.5 rounded border border-purple-900/60 bg-purple-950/60 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-purple-300"
                title="Superadmin"
              >
                <Crown size={9} />
              </span>
            )}
          </div>
        )}
      </div>

      {/* CollapseToggle — desktop only; the mobile drawer is always expanded. */}
      <button
        type="button"
        onClick={toggleCollapsed}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className={cn(
          'hidden items-center gap-2 rounded-md border border-white/[0.06] text-xs text-zinc-400 transition-colors hover:bg-white/[0.04] hover:text-zinc-100 lg:inline-flex',
          collapsed ? 'h-8 w-8 justify-center p-0' : 'w-full justify-center px-2 py-1.5',
        )}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        {!collapsed && <span>Collapse</span>}
      </button>
    </div>
  );

  // ---- Auth gate ----
  // The panel used to render a bare black rectangle while /me/ was in flight
  // and, for a non-admin, silently router.replace('/') — which reads as "the
  // link is broken" rather than "you don't have access".
  if (status === 'loading') {
    return <GateScreen icon={<Loader2 size={22} className="animate-spin text-[#f95400]" />} title="Checking access…" body="Verifying your admin role with the Loudrr backend." />;
  }

  if (status === 'forbidden') {
    return (
      <GateScreen
        icon={<ShieldAlert size={22} className="text-amber-400" />}
        title="This Telegram account isn't an admin"
        body="The Loudrr admin panel is limited to accounts with an admin or superadmin role. If you think that's wrong, ask a superadmin to grant the role, then reload."
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <Button variant="secondary" onClick={reload}>
              <RefreshCw size={14} /> Retry
            </Button>
            <Link href="/">
              <Button variant="ghost">Back to Loudrr</Button>
            </Link>
          </div>
        }
      />
    );
  }

  if (status === 'error') {
    return (
      <GateScreen
        icon={<ShieldAlert size={22} className="text-red-400" />}
        title="Couldn't verify admin access"
        body={error ?? 'The backend did not respond. It may be restarting.'}
        action={
          <Button variant="secondary" onClick={reload}>
            <RefreshCw size={14} /> Retry
          </Button>
        }
      />
    );
  }

  // globals.css locks html/body overflow for the Telegram mini-app, so the
  // admin shell owns its own scroll. Root is h-screen + overflow-hidden;
  // the inner <main> is the actual scroll container.
  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0a0a] text-white">
      <Sidebar
        collapsed={collapsed}
        items={navItems}
        footer={footer}
        mobileOpen={mobileNavOpen}
        onMobileClose={closeMobileNav}
      />

      {/* min-w-0 is load-bearing: without it this flex child refuses to shrink
          below its content's intrinsic width and the page overflows the
          viewport instead of scrolling inside <main>. */}
      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Ambient orange orb in the top-right (matches the landing page's
            decorative-orb language). Pointer-events: none so it doesn't eat
            clicks. Fixed to the main area, never scrolls with content. */}
        <div
          aria-hidden
          className="orb orb-orange pointer-events-none absolute -right-40 -top-40 h-[480px] w-[480px] opacity-60"
        />
        {/* Subtle noise texture overlay for organic feel — also from landing. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.025] mix-blend-soft-light"
          style={{
            backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
            backgroundRepeat: 'repeat',
          }}
        />
        <TopBar
          breadcrumb={breadcrumb}
          onMenuClick={() => setMobileNavOpen(true)}
          menuOpen={mobileNavOpen}
        />
        {/* overflow-x-auto (not hidden) so a page whose table is genuinely
            wider than the viewport can still be reached by swiping, even if
            it forgot to wrap itself in <TableScroll>. */}
        <main className="scrollbar-content relative min-w-0 flex-1 overflow-y-auto overflow-x-auto p-4 lg:p-8">
          {children}
        </main>
      </div>

      <Toaster
        theme="dark"
        position={toastPosition}
        toastOptions={{
          style: {
            background: '#111',
            border: '1px solid rgba(255,255,255,0.08)',
            color: '#fff',
          },
        }}
      />
    </div>
  );
}

function GateScreen({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-screen w-full items-center justify-center bg-[#0a0a0a] p-6 text-white">
      <div className="w-full max-w-sm text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full border border-white/[0.08] bg-white/[0.03]">
          {icon}
        </div>
        <h1 className="mt-4 font-syne text-lg font-bold tracking-tight text-white">{title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-zinc-400">{body}</p>
        {action && <div className="mt-5">{action}</div>}
      </div>
    </div>
  );
}
