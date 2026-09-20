'use client';

import { useEffect, useRef } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { AnimatePresence, motion } from 'framer-motion';
import { X, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface SidebarItem {
  href: string;
  label: string;
  icon: LucideIcon;
  badge?: string | number | null;
}

interface SidebarProps {
  collapsed: boolean;
  items: SidebarItem[];
  footer?: React.ReactNode;
  /** Under `lg` the sidebar is an overlay drawer; this is its open state. */
  mobileOpen?: boolean;
  /** Called on Escape, backdrop click, close button and route change. */
  onMobileClose?: () => void;
}

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Admin navigation.
 *
 * Desktop (≥lg): a permanent 260px (72px collapsed) column.
 * Mobile (<lg): hidden entirely and reachable as an overlay drawer from the
 * TopBar hamburger. It used to be a hard `w-[260px]` with no breakpoint, which
 * left a 390px phone with a ~130px content column and every table's action
 * buttons ~1000px off-screen with no way to scroll to them.
 */
export function Sidebar({ collapsed, items, footer, mobileOpen = false, onMobileClose }: SidebarProps) {
  const pathname = usePathname();
  const drawerRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // Live ref: pages pass an inline arrow, and we don't want the effects below
  // re-running (and re-stealing focus) on every render.
  const onCloseRef = useRef(onMobileClose);
  useEffect(() => {
    onCloseRef.current = onMobileClose;
  });

  // Close on navigation — otherwise tapping a nav link leaves the drawer
  // covering the page the admin just asked for.
  useEffect(() => {
    if (mobileOpen) onCloseRef.current?.();
    // Intentionally keyed on pathname only: this fires ON navigation, and
    // adding `mobileOpen` would close the drawer the moment it opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // Escape, focus containment, scroll lock, focus restore.
  useEffect(() => {
    if (!mobileOpen) return;

    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const raf = requestAnimationFrame(() => {
      const root = drawerRef.current;
      root?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    });

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (e.key !== 'Tab') return;
      const root = drawerRef.current;
      if (!root) return;
      const nodes = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null,
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (!active || !root.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKey, true);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('keydown', onKey, true);
      document.body.style.overflow = previousOverflow;
      const restore = restoreFocusRef.current;
      restoreFocusRef.current = null;
      if (restore && document.contains(restore)) restore.focus();
    };
  }, [mobileOpen]);

  return (
    <>
      {/* ---- Desktop rail ---- */}
      <aside
        className={cn(
          'sticky top-0 z-10 hidden h-screen shrink-0 flex-col border-r border-white/[0.08] bg-[#0a0a0a] transition-[width] duration-300 ease-out lg:flex',
          collapsed ? 'w-[72px]' : 'w-[260px]',
        )}
      >
        <SidebarBody collapsed={collapsed} items={items} footer={footer} pathname={pathname} />
      </aside>

      {/* ---- Mobile drawer ---- */}
      <AnimatePresence>
        {mobileOpen && (
          <div className="fixed inset-0 z-50 lg:hidden" role="presentation">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="absolute inset-0 bg-black/70 backdrop-blur-sm"
              onClick={() => onCloseRef.current?.()}
              aria-hidden
            />
            <motion.div
              ref={drawerRef}
              role="dialog"
              aria-modal="true"
              aria-label="Admin navigation"
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'tween', ease: 'easeOut', duration: 0.2 }}
              className="absolute inset-y-0 left-0 flex w-[276px] max-w-[85vw] flex-col border-r border-white/[0.08] bg-[#0a0a0a] shadow-2xl shadow-black/60"
            >
              <button
                type="button"
                onClick={() => onCloseRef.current?.()}
                aria-label="Close navigation"
                className="absolute right-2 top-[18px] z-10 rounded-md p-2 text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f95400]/40"
              >
                <X size={18} />
              </button>
              {/* Always expanded in the drawer — a 72px icon rail inside an
                  overlay would be pointlessly cryptic. */}
              <SidebarBody collapsed={false} items={items} footer={footer} pathname={pathname} />
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </>
  );
}

function SidebarBody({
  collapsed,
  items,
  footer,
  pathname,
}: {
  collapsed: boolean;
  items: SidebarItem[];
  footer?: React.ReactNode;
  pathname: string;
}) {
  return (
    <>
      {/* Logo — matches landing page treatment exactly:
          44px icon with orange blur-xl glow halo behind, "Loudrr" in font-syne
          font-bold text-[#f95400] tracking-tight, all wrapped in `group` so
          the halo brightens on hover. (landing source: app/page.tsx:104-117) */}
      <div
        className={cn(
          'flex h-[68px] shrink-0 items-center border-b border-white/[0.06]',
          collapsed ? 'justify-center px-0' : 'px-5',
        )}
      >
        <Link href="/admin" className="group flex cursor-pointer items-center gap-2.5">
          <div className="relative h-11 w-11 shrink-0">
            <Image
              src="/loudrr-icon.png"
              alt="Loudrr"
              fill
              priority
              className="object-contain transition-opacity duration-500"
            />
            <div className="absolute inset-0 rounded-full bg-[#f95400] opacity-30 blur-xl transition-opacity duration-500 group-hover:opacity-50" />
          </div>
          {!collapsed && (
            <div className="flex items-center gap-2 leading-none">
              <span className="font-syne text-2xl font-bold tracking-tight text-[#f95400]">
                Loudrr
              </span>
              <span className="rounded-md border border-white/[0.08] bg-white/[0.04] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-zinc-400">
                Admin
              </span>
            </div>
          )}
        </Link>
      </div>

      {/* Nav items — adopt landing's button language: rounded-xl, border-white/10,
          bg-white/[0.02], active:scale-[0.98], transition-all duration-300.
          Active state gets an orange glow shadow + brighter gradient bg. */}
      <nav className="scrollbar-content flex-1 overflow-y-auto px-3 py-4">
        <ul className="flex flex-col gap-1.5">
          {items.map(({ href, label, icon: Icon, badge }) => {
            const active = href === '/admin' ? pathname === '/admin' : pathname.startsWith(href);
            return (
              <li key={href}>
                <motion.div whileTap={{ scale: 0.98 }}>
                  <Link
                    href={href}
                    title={collapsed ? label : undefined}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'group/nav relative flex items-center gap-3 overflow-hidden rounded-xl border text-sm font-medium transition-all duration-300',
                      collapsed ? 'justify-center px-0 py-2.5' : 'px-3 py-2.5',
                      active
                        ? 'border-[#f95400]/30 bg-gradient-to-r from-[#f95400]/[0.16] via-[#f95400]/[0.06] to-transparent text-white shadow-[0_0_24px_-8px_rgba(249,84,0,0.6)]'
                        : 'border-white/[0.06] bg-white/[0.02] text-zinc-400 hover:border-white/[0.12] hover:bg-white/[0.04] hover:text-white',
                    )}
                  >
                    {/* Active state orange left accent stripe */}
                    {active && (
                      <span
                        aria-hidden
                        className="pointer-events-none absolute inset-y-0 left-0 w-[3px] bg-[#f95400] shadow-[0_0_8px_rgba(249,84,0,0.8)]"
                      />
                    )}
                    <Icon
                      size={16}
                      className={cn(
                        'shrink-0 transition-colors duration-300',
                        active ? 'text-[#f95400]' : 'text-zinc-400 group-hover/nav:text-zinc-200',
                      )}
                    />
                    {!collapsed && (
                      <>
                        <span className="flex-1 truncate">{label}</span>
                        {badge !== undefined && badge !== null && badge !== '' && (
                          <span
                            className={cn(
                              'ml-auto inline-flex min-w-[20px] items-center justify-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold leading-none tabular-nums',
                              active
                                ? 'bg-[#f95400] text-black shadow-[0_0_12px_rgba(249,84,0,0.5)]'
                                : 'bg-white/[0.08] text-zinc-200',
                            )}
                          >
                            {badge}
                          </span>
                        )}
                      </>
                    )}
                  </Link>
                </motion.div>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer slot */}
      {footer && (
        <div
          className={cn(
            'shrink-0 border-t border-white/[0.06] p-3',
            collapsed && 'flex flex-col items-center',
          )}
        >
          {footer}
        </div>
      )}
    </>
  );
}
