"use client";

import { useEffect, useState } from "react";
import { LockKey } from "@phosphor-icons/react";
import { WaitlistButton } from "./Waitlist";

// Gates an unfinished feature. Superadmins see it; everyone else gets a "coming soon" screen.
// Enable superadmin by visiting any locked page with ?role=superadmin (persists); ?role=guest clears it.
export function ComingSoon({ title, blurb, children }: { title: string; blurb?: string; children: React.ReactNode }) {
  const [su, setSu] = useState<boolean | null>(null);

  useEffect(() => {
    try {
      const role = new URLSearchParams(window.location.search).get("role");
      if (role === "superadmin") localStorage.setItem("loudrr.role", "superadmin");
      if (role === "guest") localStorage.removeItem("loudrr.role");
      setSu(localStorage.getItem("loudrr.role") === "superadmin");
    } catch {
      setSu(false);
    }
  }, []);

  if (su === null) return <div className="min-h-[40vh]" />; // avoid flash before role resolves
  if (su) return <>{children}</>;

  return (
    <div className="mx-auto max-w-2xl">
      <div className="glass-card relative overflow-hidden p-10 text-center sm:p-14">
        <div className="orb orb-orange pointer-events-none absolute -top-20 left-1/2 h-56 w-56 -translate-x-1/2 opacity-40" />
        <div className="relative mx-auto grid h-12 w-12 place-items-center rounded-full border border-white/10 bg-white/[0.04] text-flare">
          <LockKey size={20} weight="fill" />
        </div>
        <h1 className="relative mt-5 font-display text-3xl font-extrabold tracking-tightest">{title}</h1>
        <div className="relative mt-3">
          <span className="inline-block rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.18em] text-bone-400">
            Coming soon
          </span>
        </div>
        <p className="relative mx-auto mt-4 max-w-md text-bone-400">
          {blurb ?? "This is in active build. Join the creator community to get early access the moment it ships."}
        </p>
        <div className="relative mt-6 flex justify-center">
          <WaitlistButton label="Join Creator Community" />
        </div>
      </div>
    </div>
  );
}
