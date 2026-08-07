"use client";

import Link from "next/link";
import Image from "next/image";
import { motion } from "framer-motion";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ComponentType } from "react";
import { House, Trophy, ChartPieSlice, TrendUp, Megaphone, Code, SignIn, SignOut, Tag, type IconProps } from "@phosphor-icons/react";
import { useAuthGate } from "../AuthGate";
import { FREE_VIEW_LIMIT, remainingFreeViews } from "@/lib/gate";

type Item = { href: string; label: string; icon: ComponentType<IconProps>; disabled?: boolean; badge?: string };

const GROUPS: { label: string; items: Item[] }[] = [
  {
    label: "Discover",
    items: [
      { href: "/", label: "Home", icon: House },
      { href: "/leaderboard", label: "Loudrr Rank", icon: Trophy },
      { href: "/kol-signals", label: "KOL Signals", icon: Megaphone, badge: "new" },
      { href: "#", label: "Mindshare", icon: ChartPieSlice, disabled: true, badge: "soon" },
      { href: "#", label: "Trending", icon: TrendUp, disabled: true, badge: "soon" },
    ],
  },
  {
    label: "Develop",
    items: [
      { href: "/developers", label: "API & Docs", icon: Code },
      { href: "/pricing", label: "Pricing", icon: Tag },
    ],
  },
];

export function Sidebar({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const { loggedIn, requireLogin, signOut } = useAuthGate();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" || pathname.startsWith("/score") : pathname.startsWith(href);

  return (
    <div className="flex h-full flex-col bg-ink-800">
      {/* brand */}
      <Link
        href="/"
        onClick={onNavigate}
        className={`flex h-14 shrink-0 items-center px-4 ${collapsed ? "justify-center px-0" : ""}`}
        aria-label="Loudrr home"
      >
        {collapsed ? (
          <Image src="/loudrr-icon.png" alt="Loudrr" width={28} height={28} className="h-7 w-7 object-contain" />
        ) : (
          <Image src="/loudrr-logo.png" alt="Loudrr" width={1262} height={311} priority className="h-7 w-auto object-contain" />
        )}
      </Link>
      <div className="mx-3 border-t border-white/[0.06]" />

      {/* nav */}
      <nav className="no-scrollbar flex-1 overflow-y-auto px-3 py-3">
        {GROUPS.map((g) => (
          <div key={g.label} className="mb-4">
            {!collapsed && (
              <div className="px-2 pb-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-bone-500">
                {g.label}
              </div>
            )}
            <div className="flex flex-col gap-0.5">
              {g.items.map((it) => {
                const Icon = it.icon;
                const active = !it.disabled && isActive(it.href);
                const cls = `group relative flex items-center gap-3 rounded-lg py-2 text-sm transition-colors ${
                  collapsed ? "justify-center px-0" : "px-3"
                } ${
                  it.disabled
                    ? "cursor-not-allowed text-bone-600/60"
                    : active
                      ? "bg-flare/10 text-bone-100"
                      : "text-bone-300 hover:bg-ink-700 hover:text-bone-100"
                }`;
                const inner = (
                  <>
                    {active && (
                      <motion.span
                        layoutId="sidebar-active"
                        className="absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-full bg-flare"
                        transition={{ type: "spring", stiffness: 500, damping: 40 }}
                      />
                    )}
                    <Icon size={18} weight={active ? "fill" : "regular"} className={active ? "text-flare" : ""} />
                    {!collapsed && <span className="flex-1 truncate font-medium">{it.label}</span>}
                    {!collapsed && it.badge && (
                      <span className="rounded-full border border-white/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide text-bone-500">
                        {it.badge}
                      </span>
                    )}
                  </>
                );
                if (it.disabled)
                  return (
                    <div key={it.label} className={cls} title="Coming soon" aria-disabled>
                      {inner}
                    </div>
                  );
                return (
                  <Link key={it.label} href={it.href} onClick={onNavigate} className={cls} title={collapsed ? it.label : undefined}>
                    {inner}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* bottom: free-lookup meter + auth */}
      <div className="border-t border-white/[0.06] p-3">
        {!collapsed && <FreeMeter />}
        {loggedIn ? (
          <button onClick={signOut} className={`btn-ghost mt-2 w-full ${collapsed ? "!px-0" : ""}`}>
            {collapsed ? <SignOut size={16} className="mx-auto" /> : "Sign out"}
          </button>
        ) : (
          <button onClick={() => requireLogin()} className={`btn-flare mt-2 w-full ${collapsed ? "!px-0" : ""}`}>
            {collapsed ? <SignIn size={16} className="mx-auto" /> : "Sign in / Upgrade"}
          </button>
        )}
      </div>
    </div>
  );
}

// metered free-lookup chip — mirrors the gate model; mount-guarded so SSR/CSR agree.
function FreeMeter() {
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => setLeft(remainingFreeViews()), []);
  if (left === null) return null;
  const used = FREE_VIEW_LIMIT - left;
  const pct = Math.min(100, (used / FREE_VIEW_LIMIT) * 100);
  return (
    <div className="rounded-lg border border-white/[0.07] bg-ink-700 px-3 py-2.5">
      <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.12em] text-bone-500">
        <span>free lookups</span>
        <span className="text-bone-300">
          {used}/{FREE_VIEW_LIMIT}
        </span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
        <div className="h-full rounded-full bg-flare transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
