import type { Metadata } from "next";
import { LockKey } from "@phosphor-icons/react/dist/ssr";
import { WaitlistButton } from "@/components/Waitlist";

export const metadata: Metadata = {
  title: "Trending",
  description: "Gainers & losers by Loudrr Score — coming soon.",
};

export default function TrendingPage() {
  return (
    <div className="mx-auto max-w-2xl">
      <div className="glass-card relative overflow-hidden p-10 text-center sm:p-14">
        <div className="orb orb-orange pointer-events-none absolute -top-20 left-1/2 h-56 w-56 -translate-x-1/2 opacity-40" />
        <div className="relative mx-auto grid h-12 w-12 place-items-center rounded-full border border-white/10 bg-white/[0.04] text-flare">
          <LockKey size={20} weight="fill" />
        </div>
        <h1 className="relative mt-5 font-display text-3xl font-extrabold tracking-tightest">Trending</h1>
        <div className="relative mt-3">
          <span className="inline-block rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.18em] text-bone-400">
            Coming soon
          </span>
        </div>
        <p className="relative mx-auto mt-4 max-w-md text-bone-400">
          Live gainers &amp; losers by Loudrr Score. In active build; join the community for early access.
        </p>
        <div className="relative mt-6 flex justify-center">
          <WaitlistButton label="Join Creator Community" />
        </div>
      </div>
    </div>
  );
}
