"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { TrendUp } from "@phosphor-icons/react";
import { getLeaderboard, USE_LIVE } from "@/lib/api";
import { avatar, LEADERBOARD } from "@/lib/mock";
import { tierFor } from "@/lib/score";

type RailItem = { handle: string; name: string; score: number };

// Auto-scrolling rail of top accounts — flows left→right, pauses on hover, each chip links to
// that profile so users can hop to the next account without leaving the page. Live = REAL
// ranked accounts only; the mock list exists purely for offline design work.
export function TrendingRail({ exclude }: { exclude?: string }) {
  const [liveItems, setLiveItems] = useState<RailItem[] | null>(null);

  useEffect(() => {
    if (!USE_LIVE) return;
    let dead = false;
    getLeaderboard(20).then((r) => {
      if (!dead) {
        setLiveItems((r?.items ?? []).map((i) => ({
          handle: i.userName, name: i.name || i.userName, score: i.score,
        })));
      }
    });
    return () => {
      dead = true;
    };
  }, []);

  const source: RailItem[] = USE_LIVE
    ? liveItems ?? []
    : LEADERBOARD.map((p) => ({ handle: p.handle, name: p.name, score: p.score }));
  const items = source.filter((p) => p.handle !== exclude).slice(0, 16);
  if (items.length === 0) return null; // loading / no data — render nothing fake
  const row = [...items, ...items]; // duplicate for a seamless loop

  return (
    <div className="marquee-wrap relative overflow-hidden rounded-2xl border border-white/[0.07] bg-ink-800/40 py-3">
      <style>{`
        @keyframes loudrr-marquee { from { transform: translateX(-50%); } to { transform: translateX(0); } }
        .marquee-track { animation: loudrr-marquee 55s linear infinite; }
        .marquee-wrap:hover .marquee-track { animation-play-state: paused; }
        @media (prefers-reduced-motion: reduce) { .marquee-track { animation: none; } }
      `}</style>
      <div className="mb-3 flex items-center gap-2 px-4 text-bone-200">
        <TrendUp size={15} weight="fill" className="text-flare" />
        <h2 className="font-display text-sm font-semibold uppercase tracking-wider">Trending accounts</h2>
      </div>
      <div className="marquee-track flex w-max gap-2.5 px-4">
        {row.map((p, i) => {
          const col = tierFor(p.score).color;
          return (
            <Link
              key={i}
              href={`/score/${p.handle}`}
              className="flex shrink-0 items-center gap-2.5 rounded-xl border border-white/[0.06] bg-ink-800/60 px-3 py-2 transition-colors hover:border-flare/30 hover:bg-ink-700"
            >
              <Image
                src={avatar(p.handle)}
                alt={p.handle}
                width={28}
                height={28}
                unoptimized
                className="h-7 w-7 shrink-0 rounded-full bg-ink-700 object-cover"
              />
              <span className="whitespace-nowrap text-sm font-medium text-bone-100">{p.name}</span>
              <span className="font-mono text-sm font-bold tabular-nums" style={{ color: col }}>
                {p.score.toLocaleString()}
              </span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
