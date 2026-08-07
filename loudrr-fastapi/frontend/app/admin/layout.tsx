'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { Toaster } from 'sonner';
import {
  ChevronLeft,
  ChevronRight,
  Crown,
  LayoutDashboard,
  Settings2,
  ShieldCheck,
  UserCheck,
  Users,
} from 'lucide-react';

import { Sidebar, type SidebarItem } from '@/components/admin/Sidebar';
import { TopBar } from '@/components/admin/TopBar';
import { adminApi } from '@/lib/api';
import { cn } from '@/lib/utils';

type AdminMe = {
  id: string;
  telegram_id: number | null;
  telegram_username: string;
  role: 'admin' | 'superadmin' | '';
};

const NAV_BASE: Array<Omit<SidebarItem, 'badge'> & { badgeKey?: 'waitlist' | 'xVerification' }> = [
  { href: '/admin', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/admin/waitlist', label: 'Waitlist', icon: UserCheck, badgeKey: 'waitlist' },
  { href: '/admin/x-verification', label: 'X Verification', icon: ShieldCheck, badgeKey: 'xVerification' },
  { href: '/admin/users', label: 'Users', icon: Users },
  { href: '/admin/site-settings', label: 'Site Settings', icon: Settings2 },
];

const COLLAPSE_KEY = 'loudrr.admin.sidebar.collapsed';

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
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [pendingWaitlist, setPendingWaitlist] = useState<number>(0);
  const [pendingX, setPendingX] = useState<number>(0);
  // `me === null` means "still loading"; a real object means authorized.
  // A non-admin gets redirected before this state ever flips off null.
  const [me, setMe] = useState<AdminMe | null>(null);

  // Gate: fetch the calling user's identity + role BEFORE mounting the
  // admin shell. If the /me/ call 403s (non-admin) or 401s (no session),
  // punt to /. Otherwise render normally. Server RBAC still enforces per-
  // endpoint access — this is a UX guard to fail fast + friendly instead
  // of loading the shell and watching every data query 403 individually.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const identity = await adminApi.me();
        if (cancelled) return;
        if (!identity.role) {
          router.replace('/');
          return;
        }
        setMe(identity);
      } catch {
        if (!cancelled) router.replace('/');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  // Restore collapsed state from localStorage on mount.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(COLLAPSE_KEY);
      if (stored === '1') setCollapsed(true);
    } catch {
      /* no-op: localStorage unavailable (SSR / privacy mode) */
    }
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0');
      } catch {
        /* no-op */
      }
      return next;
    });
  }, []);

  // Refresh pending counts on every route change.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [waitlist, xVer] = await Promise.all([
          adminApi.pendingWaitlist(50),
          adminApi.pendingXVerifications(50),
        ]);
        if (!cancelled) {
          setPendingWaitlist(Array.isArray(waitlist) ? waitlist.length : 0);
          setPendingX(Array.isArray(xVer) ? xVer.length : 0);
        }
      } catch {
        // Silently ignore — badges are nice-to-have, not blocking.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const navItems: SidebarItem[] = NAV_BASE.map(({ badgeKey, ...item }) => {
    if (badgeKey === 'waitlist' && pendingWaitlist > 0) return { ...item, badge: pendingWaitlist };
    if (badgeKey === 'xVerification' && pendingX > 0) return { ...item, badge: pendingX };
    return item;
  });

  const breadcrumb = breadcrumbFromPathname(pathname);

  const userDisplayName = me?.telegram_username || (me?.telegram_id ? `@${me.telegram_id}` : '—');
  const userInitial = (userDisplayName[0] || '?').toUpperCase();

  const footer = (
    <div className={cn('flex flex-col gap-2', collapsed && 'items-center')}>
      {/* UserPill — driven by /api/admin/me/ (the gate ensures `me` is set
          before the shell renders, so we don't need a loading placeholder). */}
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
            <span className="truncate text-xs font-medium text-zinc-200">
              {userDisplayName}
            </span>
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

      {/* CollapseToggle */}
      <button
        type="button"
        onClick={toggleCollapsed}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className={cn(
          'inline-flex items-center gap-2 rounded-md border border-white/[0.06] text-xs text-zinc-500 transition-colors hover:bg-white/[0.04] hover:text-zinc-200',
          collapsed ? 'h-8 w-8 justify-center p-0' : 'w-full justify-center px-2 py-1.5',
        )}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        {!collapsed && <span>Collapse</span>}
      </button>
    </div>
  );

  // Gate: don't render the shell until /me/ resolves. On the non-admin path
  // we've already fired router.replace('/') so we'd unmount immediately —
  // no shell flash, no data fetches. Simple full-screen bg keeps the visual
  // continuity while the auth check flies.
  if (!me) {
    return <div className="h-screen w-screen bg-[#0a0a0a]" />;
  }

  // globals.css locks html/body overflow for the Telegram mini-app, so the
  // admin shell owns its own scroll. Root is h-screen + overflow-hidden;
  // the inner <main> is the actual scroll container.
  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0a0a] text-white">
      <Sidebar collapsed={collapsed} items={navItems} footer={footer} />

      <div className="relative flex flex-1 flex-col overflow-hidden">
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
        <TopBar breadcrumb={breadcrumb} />
        <main className="scrollbar-content relative flex-1 overflow-y-auto p-8">{children}</main>
      </div>

      <Toaster
        theme="dark"
        position="top-right"
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
