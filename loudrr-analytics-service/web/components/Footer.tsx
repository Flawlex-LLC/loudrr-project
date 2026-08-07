import Link from "next/link";
import { XBrandIcon } from "./icons/BrandIcons";
import { Logo } from "./Logo";

const COLS = [
  {
    head: "Product",
    links: [
      { label: "Score lookup", href: "/" },
      { label: "Leaderboard", href: "/leaderboard" },
      { label: "API", href: "/developers" },
    ],
  },
  {
    head: "Verticals",
    links: [
      { label: "Crypto", href: "/leaderboard" },
      { label: "AI / Tech — soon", href: "/leaderboard" },
      { label: "Stocks — soon", href: "/leaderboard" },
    ],
  },
  {
    head: "Company",
    links: [
      { label: "How scoring works", href: "/developers#method" },
      { label: "Contact sales", href: "/developers#sales" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="mt-24 border-t border-white/[0.07]">
      <div className="mx-auto grid max-w-6xl gap-10 px-5 py-14 sm:grid-cols-2 md:grid-cols-[1.4fr_1fr_1fr_1fr]">
        <div>
          <Logo />
          <p className="mt-4 max-w-xs text-sm leading-relaxed text-bone-500">
            Influence measured by quality, not follower counts — the score crypto builders actually trust.
          </p>
          <a
            href="https://x.com"
            target="_blank"
            rel="noreferrer"
            className="mt-5 inline-flex h-9 w-9 items-center justify-center rounded-full border border-white/10 text-bone-400 transition-colors hover:border-flare/50 hover:text-flare"
            aria-label="Loudrr on X"
          >
            <XBrandIcon size={16} />
          </a>
        </div>
        {COLS.map((c) => (
          <div key={c.head}>
            <div className="eyebrow mb-4">{c.head}</div>
            <ul className="space-y-2.5">
              {c.links.map((l) => (
                <li key={l.label}>
                  <Link href={l.href} className="text-sm text-bone-400 transition-colors hover:text-bone-100">
                    {l.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-white/[0.07]">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-5 py-5 sm:flex-row">
          <span className="font-mono text-[11px] text-bone-600">© {2026} Loudrr · all signals reserved</span>
          <span className="font-mono text-[11px] text-bone-600">
            Scores are estimates · not financial advice
          </span>
        </div>
      </div>
    </footer>
  );
}
