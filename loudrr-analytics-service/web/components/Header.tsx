"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { List, SignOut, UserCircle, X } from "@phosphor-icons/react";
import { Logo } from "./Logo";
import { WaitlistButton } from "./Waitlist";
import { useAuthGate } from "./AuthGate";

const NAV = [
  { href: "#campaigns", label: "Campaigns" },
  { href: "/mindshare", label: "Mindshare" },
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/pricing", label: "Pricing" },
];

export function Header() {
  const pathname = usePathname();
  const { loggedIn, requireLogin, signOut } = useAuthGate();
  const [open, setOpen] = useState(false);

  const active = (href: string) => (href === "/" ? pathname === "/" || pathname.startsWith("/score") : pathname.startsWith(href));

  return (
    <header className="sticky top-0 z-50 border-b border-white/[0.07] bg-ink-900/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-5">
        <div className="flex items-center gap-8">
          <Logo priority />
          <nav className="hidden items-center gap-1 md:flex">
            {NAV.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={`relative rounded-lg px-3 py-2 font-mono text-sm transition-colors ${
                  active(n.href) ? "text-bone-100" : "text-bone-500 hover:text-bone-200"
                }`}
              >
                {n.label}
                {active(n.href) && (
                  <span className="absolute inset-x-3 -bottom-[1px] h-[2px] rounded-full bg-flare" />
                )}
              </Link>
            ))}
          </nav>
        </div>

        <div className="hidden items-center gap-3 md:flex">
          {loggedIn && (
            <button onClick={signOut} className="btn-ghost group !px-3" title="Sign out">
              <UserCircle size={18} weight="fill" className="text-flare" />
              <SignOut size={14} className="text-bone-600 transition-colors group-hover:text-bone-300" />
            </button>
          )}
          <Link href="/leaderboard" className="btn-ghost">Launch app</Link>
          <WaitlistButton />
        </div>

        {/* mobile */}
        <button
          className="md:hidden text-bone-200"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          aria-controls="mobile-nav"
        >
          {open ? <X size={24} /> : <List size={24} />}
        </button>
      </div>

      {open && (
        <div id="mobile-nav" className="border-t border-white/[0.07] bg-ink-900/95 px-5 py-4 md:hidden">
          <nav className="flex flex-col gap-1">
            {NAV.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                onClick={() => setOpen(false)}
                className={`rounded-lg px-3 py-2.5 font-mono text-sm ${active(n.href) ? "bg-white/5 text-flare" : "text-bone-300"}`}
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="mt-3 flex items-center gap-3">
            {loggedIn ? (
              <button onClick={signOut} className="btn-ghost flex-1">
                Sign out
              </button>
            ) : (
              <button onClick={() => requireLogin()} className="btn-ghost flex-1">
                Sign in
              </button>
            )}
            <WaitlistButton className="flex-1 justify-center" />
          </div>
        </div>
      )}
    </header>
  );
}
