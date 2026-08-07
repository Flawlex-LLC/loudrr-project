"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CaretRight, List, SidebarSimple } from "@phosphor-icons/react";
import { WaitlistButton } from "../Waitlist";
import { useAuthGate } from "../AuthGate";

// breadcrumb from the current path; parents are muted links, the current crumb is solid.
function useCrumbs(pathname: string): { label: string; href?: string }[] {
  const seg = pathname.split("/").filter(Boolean);
  if (seg[0] === "leaderboard") return [{ label: "Loudrr Rank" }];
  if (seg[0] === "kol-signals") {
    return seg[1]
      ? [{ label: "KOL Signals", href: "/kol-signals" }, { label: "Token" }]
      : [{ label: "KOL Signals" }];
  }
  if (seg[0] === "trending") return [{ label: "Trending" }];
  if (seg[0] === "developers") return [{ label: "API & Docs" }];
  if (seg[0] === "score") {
    const handle = seg[1] ? decodeURIComponent(seg[1]) : "";
    return [{ label: "Home", href: "/" }, { label: handle ? `@${handle}` : "Profile" }];
  }
  return [{ label: "Home" }];
}

export function TopBar({
  onToggleCollapse,
  onOpenMobile,
}: {
  onToggleCollapse: () => void;
  onOpenMobile: () => void;
}) {
  const pathname = usePathname();
  const crumbs = useCrumbs(pathname);
  const { loggedIn, requireLogin, signOut } = useAuthGate();

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-white/[0.07] bg-ink-800/80 backdrop-blur-xl px-3 sm:px-4">
      <button
        onClick={onToggleCollapse}
        className="hidden h-8 w-8 items-center justify-center rounded-lg text-bone-400 transition-colors hover:bg-ink-700 hover:text-bone-100 lg:inline-flex"
        aria-label="Toggle sidebar"
      >
        <SidebarSimple size={18} />
      </button>
      <button
        onClick={onOpenMobile}
        className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-bone-400 transition-colors hover:bg-ink-700 hover:text-bone-100 lg:hidden"
        aria-label="Open menu"
      >
        <List size={20} />
      </button>

      {/* breadcrumb */}
      <nav className="flex min-w-0 items-center gap-1.5 font-mono text-sm">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <span key={c.label} className="flex min-w-0 items-center gap-1.5">
              {i > 0 && <CaretRight size={12} className="shrink-0 text-bone-600" />}
              {c.href && !last ? (
                <Link href={c.href} className="truncate text-bone-500 transition-colors hover:text-bone-200">
                  {c.label}
                </Link>
              ) : (
                <span className={last ? "truncate text-bone-100" : "truncate text-bone-500"}>{c.label}</span>
              )}
            </span>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        <WaitlistButton label="Join Creator Community" className="hidden !px-4 !py-2 !text-xs sm:inline-flex" />
        {loggedIn ? (
          <button onClick={signOut} className="btn-ghost !px-3 !py-1.5 !text-xs">
            Account
          </button>
        ) : (
          <button onClick={() => requireLogin()} className="btn-ghost !px-3 !py-1.5 !text-xs">
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}
