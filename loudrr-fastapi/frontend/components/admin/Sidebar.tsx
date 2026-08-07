'use client';

import Image from 'next/image';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { motion } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
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
}

export function Sidebar({ collapsed, items, footer }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        'sticky top-0 z-10 flex h-screen flex-col border-r border-white/[0.08] bg-[#0a0a0a] transition-[width] duration-300 ease-out',
        collapsed ? 'w-[72px]' : 'w-[260px]'
      )}
    >
      {/* Logo — matches landing page treatment exactly:
          44px icon with orange blur-xl glow halo behind, "Loudrr" in font-syne
          font-bold text-[#f95400] tracking-tight, all wrapped in `group` so
          the halo brightens on hover. (landing source: app/page.tsx:104-117) */}
      <div
        className={cn(
          'flex h-[68px] shrink-0 items-center border-b border-white/[0.06]',
          collapsed ? 'justify-center px-0' : 'px-5'
        )}
      >
        <Link href="/admin" className="group flex items-center gap-2.5 cursor-pointer">
          <div className="relative h-11 w-11 shrink-0">
            <Image
              src="/loudrr-icon.png"
              alt="Loudrr"
              fill
              priority
              className="transition-opacity duration-500 object-contain"
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
      <nav className="flex-1 overflow-y-auto px-3 py-4 scrollbar-content">
        <ul className="flex flex-col gap-1.5">
          {items.map(({ href, label, icon: Icon, badge }) => {
            const active = href === '/admin' ? pathname === '/admin' : pathname.startsWith(href);
            return (
              <li key={href}>
                <motion.div whileTap={{ scale: 0.98 }}>
                  <Link
                    href={href}
                    title={collapsed ? label : undefined}
                    className={cn(
                      'group/nav relative flex items-center gap-3 overflow-hidden rounded-xl border text-sm font-medium transition-all duration-300',
                      collapsed ? 'justify-center px-0 py-2.5' : 'px-3 py-2.5',
                      active
                        ? 'border-[#f95400]/30 bg-gradient-to-r from-[#f95400]/[0.16] via-[#f95400]/[0.06] to-transparent text-white shadow-[0_0_24px_-8px_rgba(249,84,0,0.6)]'
                        : 'border-white/[0.06] bg-white/[0.02] text-zinc-400 hover:border-white/[0.12] hover:bg-white/[0.04] hover:text-white'
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
                        active ? 'text-[#f95400]' : 'text-zinc-500 group-hover/nav:text-zinc-300'
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
                                : 'bg-white/[0.08] text-zinc-300'
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
            collapsed && 'flex flex-col items-center'
          )}
        >
          {footer}
        </div>
      )}
    </aside>
  );
}
