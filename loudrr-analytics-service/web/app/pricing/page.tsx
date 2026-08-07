import type { Metadata } from "next";
import Link from "next/link";
import { Check } from "@phosphor-icons/react/dist/ssr";
import { WaitlistButton } from "@/components/Waitlist";
import { BorderBeam } from "@/components/ui/border-beam";

export const metadata: Metadata = {
  title: "Pricing",
  description: "Start free. Upgrade for unlimited lookups, alerts, and the Loudrr API.",
};

type Tier = {
  name: string;
  price: string;
  note: string;
  highlight: boolean;
  blurb: string;
  features: string[];
  cta: { label?: string; href?: string; waitlist?: boolean };
};

const TIERS: Tier[] = [
  {
    name: "Free",
    price: "$0",
    note: "forever",
    highlight: false,
    blurb: "For anyone curious about influence.",
    features: [
      "Score lookup for any handle",
      "Public leaderboard & mindshare",
      "3 full profiles / day",
      "Smart-followers preview",
    ],
    cta: { label: "Start free", href: "/" },
  },
  {
    name: "Creator",
    price: "$29",
    note: "/mo",
    highlight: true,
    blurb: "For creators & teams who live on X.",
    features: [
      "Everything in Free",
      "Unlimited profile lookups",
      "Smart mentions & watchlist",
      "Score alerts & weekly digest",
      "Compare accounts head-to-head",
      "API key — 10k calls / mo",
    ],
    cta: { waitlist: true },
  },
  {
    name: "Enterprise",
    price: "Custom",
    note: "",
    highlight: false,
    blurb: "For funds, exchanges & platforms.",
    features: [
      "Everything in Creator",
      "High-volume API & bulk scoring",
      "Custom verticals (AI, stocks)",
      "Data exports & webhooks",
      "Priority support & SLA",
    ],
    cta: { label: "Contact sales", href: "/developers#sales" },
  },
];

export default function PricingPage() {
  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-10 text-center">
        <p className="eyebrow">Pricing</p>
        <h1 className="mt-2 font-display text-4xl font-extrabold tracking-tightest sm:text-5xl">
          Simple, creator-friendly pricing
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-bone-400">
          Start free. Upgrade when you want unlimited lookups, alerts, and the API.
        </p>
      </header>

      <div className="grid items-start gap-5 md:grid-cols-3">
        {TIERS.map((t) => (
          <div key={t.name} className={`relative overflow-hidden p-6 ${t.highlight ? "glass-card" : "panel"}`}>
            {t.highlight && <BorderBeam size={220} duration={14} />}
            {t.highlight && (
              <span className="absolute right-4 top-4 rounded-full bg-flare/15 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-flare">
                Popular
              </span>
            )}
            <div className="font-display text-lg font-bold">{t.name}</div>
            <div className="mt-3 flex items-baseline gap-1.5">
              <span className="font-mono text-4xl font-bold text-bone-100">{t.price}</span>
              {t.note && <span className="font-mono text-sm text-bone-500">{t.note}</span>}
            </div>
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
              {t.cta.waitlist ? (
                <WaitlistButton label="Join Creator Community" className="w-full" />
              ) : (
                <Link href={t.cta.href ?? "/"} className={`${t.highlight ? "btn-flare" : "btn-ghost"} w-full`}>
                  {t.cta.label}
                </Link>
              )}
            </div>
          </div>
        ))}
      </div>

      <p className="mt-8 text-center font-mono text-[11px] text-bone-600">
        Prices in USD · cancel anytime · the public score endpoint is always free
      </p>
    </div>
  );
}
