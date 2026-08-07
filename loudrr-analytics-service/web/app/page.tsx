import Link from "next/link";
import {
  ArrowRight, Broadcast, ChartLineUp, Check, Crown, Gauge, Lightning,
  MagnifyingGlass, Megaphone, Ranking, TrendUp, Wallet,
} from "@phosphor-icons/react/dist/ssr";
import { SearchBar } from "@/components/SearchBar";
import { WaitlistButton } from "@/components/Waitlist";

// Home = the marketing/selling page, inside the app-shell. Loudrr = Cookie3 (onchain+offchain data)
// + Scal3 (creator campaigns) + Tunnl (automated, verifiable payouts). Measure → Activate → Pay → Prove.

const STATS = [
  { v: "33k+", l: "accounts ranked" },
  { v: "700+", l: "vetted creators" },
  { v: "hourly", l: "mindshare updates" },
  { v: "on + offchain", l: "data layers" },
];

// data-forward hero moment — live mindshare snapshot
const LIVE = [
  { t: "BTC", ms: "15.2%", d: "+3.5", up: true },
  { t: "SOL", ms: "9.0%", d: "+0.9", up: true },
  { t: "HYPERLIQUID", ms: "3.4%", d: "-0.3", up: false },
  { t: "POLYMARKET", ms: "2.4%", d: "+0.9", up: true },
  { t: "PUMP", ms: "1.7%", d: "+0.9", up: true },
];

const PILLARS = [
  {
    tag: "Measure", icon: ChartLineUp, title: "Mindshare & attribution",
    body: "Onchain + offchain. Share-of-voice, wallet journeys, and what actually converts — updated hourly.",
  },
  {
    tag: "Activate", icon: Megaphone, title: "Creator campaigns",
    body: "Matched to vetted creators by audience quality, not follower counts. Launch in days, not weeks.",
  },
  {
    tag: "Pay", icon: Wallet, title: "Automated payouts",
    body: "Creators paid automatically on delivery — verifiable onchain settlement. No spreadsheets, no chasing.",
  },
  {
    tag: "Prove", icon: Gauge, title: "Real-time metrics",
    body: "Live dashboards: mindshare lift, engagement, ROI. Prove the impact of every dollar.",
  },
];

const STEPS = [
  { n: "01", t: "Track", d: "See who moves your niche — mindshare, influence, onchain impact." },
  { n: "02", t: "Activate", d: "Launch a campaign to the right vetted creators." },
  { n: "03", t: "Pay", d: "Payouts settle automatically on delivery." },
  { n: "04", t: "Prove", d: "Watch the mindshare lift in real time." },
];

const PACKAGES = [
  {
    icon: Lightning, name: "Spark", price: "From $1.5k", popular: false,
    blurb: "A focused push for a launch or announcement.",
    features: ["10–15 vetted creators", "1-week coordinated push", "Mindshare + engagement report", "Automated payouts"],
  },
  {
    icon: TrendUp, name: "Surge", price: "From $6k", popular: true,
    blurb: "A sustained campaign to own a narrative.",
    features: ["30–50 creators across tiers", "3-week campaign", "Live mindshare dashboard", "Competitor benchmarking", "Movers alerts"],
  },
  {
    icon: Crown, name: "Takeover", price: "Custom", popular: false,
    blurb: "Full-network, always-on presence.",
    features: ["Our full creator network", "Custom vertical & KOL targeting", "Dedicated campaign manager", "API, exports & history"],
  },
];

export default function Home() {
  return (
    <div className="space-y-16 sm:space-y-24">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-ink-800/40 p-7 sm:p-12">
        <div className="orb orb-orange pointer-events-none absolute -right-24 -top-32 h-[28rem] w-[28rem] opacity-45" />
        <div className="relative grid items-center gap-10 lg:grid-cols-[1.15fr_0.85fr]">
          <div>
            <p className="eyebrow">Onchain + offchain growth for crypto</p>
            <h1 className="mt-4 font-display text-4xl font-extrabold leading-[0.98] tracking-tightest sm:text-6xl">
              Your crypto <span className="orange-gradient-text">growth engine</span>.
            </h1>
            <p className="mt-5 max-w-lg text-lg text-bone-300">
              Track who moves crypto, activate the right creators, pay them automatically — and prove the lift in real time.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="#campaigns" className="btn-flare !px-6 !py-3 text-base">
                Run a campaign <ArrowRight size={16} weight="bold" />
              </Link>
              <Link href="/leaderboard" className="btn-ghost !px-6 !py-3 text-base">Explore the app</Link>
            </div>
            <div className="mt-8 max-w-md">
              <div className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-ink-700/40 px-3 py-1.5">
                <MagnifyingGlass size={16} className="shrink-0 text-bone-500" />
                <span className="font-mono text-xs text-bone-500">Look up any X account, free —</span>
              </div>
              <div className="mt-2"><SearchBar size="lg" /></div>
            </div>
          </div>

          {/* live mindshare card — data-forward moment */}
          <div className="panel p-4 sm:p-5">
            <div className="flex items-center justify-between">
              <span className="eyebrow">Live · Crypto mindshare</span>
              <span className="flex items-center gap-1.5 font-mono text-[10px] text-up">
                <span className="h-1.5 w-1.5 rounded-full bg-up" /> live
              </span>
            </div>
            <div className="mt-3 divide-y divide-white/[0.06]">
              {LIVE.map((r, i) => (
                <div key={r.t} className="flex items-center gap-3 py-2.5">
                  <span className="w-4 font-mono text-xs text-bone-600 tnum">{i + 1}</span>
                  <span className="flex-1 truncate font-medium text-bone-100">{r.t}</span>
                  <span className="font-mono text-sm text-bone-200 tnum">{r.ms}</span>
                  <span className={`w-12 text-right font-mono text-xs tnum ${r.up ? "text-up" : "text-down"}`}>
                    {r.d}%
                  </span>
                </div>
              ))}
            </div>
            <Link href="/mindshare" className="mt-2 flex items-center justify-center gap-1 rounded-lg py-2 font-mono text-xs text-bone-400 transition-colors hover:text-flare">
              Open mindshare <ArrowRight size={13} weight="bold" />
            </Link>
          </div>
        </div>

        {/* stat strip */}
        <div className="relative mt-10 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-white/[0.08] sm:grid-cols-4">
          {STATS.map((s) => (
            <div key={s.l} className="bg-ink-700/40 px-4 py-4">
              <div className="font-display text-xl font-bold tracking-tightest sm:text-2xl">{s.v}</div>
              <div className="mt-0.5 font-mono text-[11px] text-bone-500">{s.l}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── one platform (the combination) ── */}
      <section>
        <div className="mb-8 max-w-2xl">
          <p className="eyebrow">One platform</p>
          <h2 className="mt-3 font-display text-3xl font-extrabold tracking-tightest sm:text-4xl">
            Your whole growth stack, combined.
          </h2>
          <p className="mt-3 text-bone-400">
            Mindshare intelligence, onchain analytics, creator campaigns and automated payouts — no more stitching five tools together.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PILLARS.map((p) => (
            <div key={p.title} className="panel p-6">
              <div className="flex items-center justify-between">
                <p.icon size={26} weight="duotone" className="text-flare" />
                <span className="tier-chip text-bone-400">{p.tag}</span>
              </div>
              <h3 className="mt-4 font-display text-lg font-bold">{p.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-bone-400">{p.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── how it works ── */}
      <section>
        <div className="mb-8 max-w-2xl">
          <p className="eyebrow">How it works</p>
          <h2 className="mt-3 font-display text-3xl font-extrabold tracking-tightest sm:text-4xl">
            Measure → Activate → Pay → Prove
          </h2>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s) => (
            <div key={s.n} className="relative rounded-2xl border border-white/[0.08] bg-ink-800/40 p-6">
              <span className="font-display text-3xl font-extrabold tracking-tightest text-flare/30">{s.n}</span>
              <h3 className="mt-2 font-display text-lg font-bold">{s.t}</h3>
              <p className="mt-1.5 text-sm text-bone-400">{s.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── dual audience ── */}
      <section className="grid gap-4 md:grid-cols-2">
        <div className="glass-card flex flex-col justify-between gap-6 p-8">
          <div>
            <Broadcast size={28} weight="duotone" className="text-flare" />
            <h3 className="mt-4 font-display text-2xl font-bold tracking-tightest">For projects</h3>
            <p className="mt-2 text-bone-400">Run campaigns through vetted creators and prove the mindshare you earn.</p>
          </div>
          <Link href="#campaigns" className="btn-flare w-full justify-center">
            Run a campaign <ArrowRight size={16} weight="bold" />
          </Link>
        </div>
        <div className="panel flex flex-col justify-between gap-6 p-8">
          <div>
            <Ranking size={28} weight="duotone" className="text-flare" />
            <h3 className="mt-4 font-display text-2xl font-bold tracking-tightest">For creators</h3>
            <p className="mt-2 text-bone-400">Get matched to campaigns you care about and paid automatically for your reach.</p>
          </div>
          <WaitlistButton label="Join Creator Community" className="w-full justify-center" />
        </div>
      </section>

      {/* ── campaign packages ── */}
      <section id="campaigns" className="scroll-mt-20">
        <div className="mb-8 max-w-2xl">
          <p className="eyebrow">For projects</p>
          <h2 className="mt-3 font-display text-3xl font-extrabold tracking-tightest sm:text-4xl">Run a campaign</h2>
          <p className="mt-3 text-bone-400">
            Buy an activation. Our vetted creators push your narrative, get paid automatically, and we measure the mindshare it earns.
          </p>
        </div>
        <div className="grid items-start gap-5 md:grid-cols-3">
          {PACKAGES.map((t) => (
            <div key={t.name} className={`relative overflow-hidden p-6 ${t.popular ? "glass-card" : "panel"}`}>
              {t.popular && (
                <span className="absolute right-4 top-4 rounded-full bg-flare/15 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-flare">
                  Popular
                </span>
              )}
              <t.icon size={24} weight="duotone" className="text-flare" />
              <div className="mt-4 font-display text-xl font-bold">{t.name}</div>
              <div className="mt-1 font-display text-2xl font-bold tracking-tightest text-bone-100">{t.price}</div>
              <p className="mt-2 text-sm text-bone-400">{t.blurb}</p>
              <ul className="mt-5 space-y-2.5">
                {t.features.map((f) => (
                  <li key={f} className="flex items-start gap-2 text-sm text-bone-300">
                    <Check size={16} weight="bold" className="mt-0.5 shrink-0 text-flare" />
                    {f}
                  </li>
                ))}
              </ul>
              <div className="mt-6">
                <WaitlistButton label={`Book ${t.name}`} className="w-full justify-center" />
              </div>
            </div>
          ))}
        </div>
        <p className="mt-6 font-mono text-[11px] text-bone-600">
          Campaigns are scoped to your goals · pricing indicative · real vetted creators · payouts settle onchain.
        </p>
      </section>

      {/* ── final cta ── */}
      <section className="relative overflow-hidden rounded-3xl border border-flare/15 bg-ink-800/40 px-6 py-14 text-center">
        <div className="orb orb-orange pointer-events-none absolute left-1/2 top-0 h-64 w-64 -translate-x-1/2 opacity-40" />
        <h2 className="relative font-display text-3xl font-extrabold tracking-tightest sm:text-4xl">
          Ready to get <span className="orange-gradient-text">loud</span>?
        </h2>
        <div className="relative mt-6 flex flex-wrap justify-center gap-3">
          <Link href="#campaigns" className="btn-flare !px-6 !py-3 text-base">
            Run a campaign <ArrowRight size={16} weight="bold" />
          </Link>
          <Link href="/leaderboard" className="btn-ghost !px-6 !py-3 text-base">Explore the app</Link>
        </div>
      </section>
    </div>
  );
}
