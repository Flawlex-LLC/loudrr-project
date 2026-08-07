"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Lightning } from "@phosphor-icons/react";
import { getKolBuzz, type BuzzBucket, type KolBuzz, type KolSignal } from "@/lib/api";
import { avatar } from "@/lib/mock";

// Social Buzz — the OFFCHAIN counterpart to the onchain KOL Rides chart. X-activity for this
// token, weighted by each caller's Loudrr influence (a Megaphone's call moves it more than a
// Whisper's), bucketed over the window. Below the bars: the score-weighted KOL signal board —
// where we beat Bitget/kolscan, which rank by raw post count from 2k-follower spam accounts.

const BUZZ_COLOR = "#58c7f3"; // cyan — matches KOL Rides / "social" hue
const PAD = { top: 16, right: 6, bottom: 18, left: 6 };

function fmtFollowers(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

const TIER_COLOR: Record<string, string> = {
  Megaphone: "#f95400",
  Amplifier: "#f5a623",
  Signal: "#58c7f3",
  Echo: "#8b93a7",
  Whisper: "#5b6270",
};

export function SocialBuzz({ contract, bump = 0 }: { contract: string; bump?: number }) {
  const [win, setWin] = useState<"7d" | "30d">("7d");
  const [data, setData] = useState<KolBuzz | null>(null);

  // `bump` ticks when the page's WebSocket reports a fresh KOL call for this token, which
  // is exactly when buzz changes. Recomputing then (rather than on a timer) keeps the board
  // live for free: buzz is a pure local eng_call query — no gateway, no external feed.
  useEffect(() => {
    let dead = false;
    // keep the current bars painted on a live refresh; only blank them when the token or
    // window actually changes, or the chart would flash a skeleton on every new call
    getKolBuzz(contract, win).then((d) => {
      if (!dead) setData(d);
    });
    return () => {
      dead = true;
    };
  }, [contract, win, bump]);

  useEffect(() => {
    setData(null);
  }, [contract, win]);

  return (
    <div className="panel p-4 sm:p-5">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-bone-200">
        <Lightning size={15} weight="fill" style={{ color: BUZZ_COLOR }} />
        <h2 className="font-display text-sm font-semibold uppercase tracking-wider">Social Buzz</h2>
        <span className="ml-auto flex items-center gap-1 rounded-full border border-white/[0.07] p-0.5">
          {(["7d", "30d"] as const).map((w) => (
            <button
              key={w}
              onClick={() => setWin(w)}
              className={`rounded-full px-2.5 py-0.5 font-mono text-[11px] transition-colors ${
                win === w ? "bg-white/[0.08] text-bone-100" : "text-bone-600 hover:text-bone-300"
              }`}
            >
              {w.toUpperCase()}
            </button>
          ))}
        </span>
      </div>
      <p className="mb-3 text-[11px] text-bone-600">
        Smart-account X activity for this token, weighted by Loudrr influence.
      </p>

      {data === null ? (
        <div className="skeleton h-[132px] w-full rounded-xl" />
      ) : data.coverage !== "tracked" ? (
        <p className="py-6 text-center font-mono text-xs text-bone-600">
          No tracked social activity in this window.
        </p>
      ) : (
        <>
          <div className="mb-3 flex items-baseline gap-2">
            <span className="font-display text-3xl font-bold tabular-nums text-bone-100">
              {data.buzzIndex.toFixed(1)}
            </span>
            <span className="font-mono text-[11px] uppercase tracking-wide text-bone-600">
              buzz index
            </span>
          </div>

          <BuzzBars series={data.series} window={win} />

          {/* score-weighted KOL signal board — the differentiator */}
          <div className="mt-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-bone-600">
                KOL signals
              </span>
              <span className="font-mono text-[10px] text-bone-600">
                {data.signals.length} accounts · ranked by influence
              </span>
            </div>
            <div className="max-h-[320px] space-y-0.5 overflow-y-auto pr-1">
              {data.signals.map((s, i) => (
                <SignalRow key={s.username} rank={i + 1} s={s} />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SignalRow({ rank, s }: { rank: number; s: KolSignal }) {
  return (
    <Link
      href={`/score/${s.username}`}
      className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-white/[0.04]"
    >
      <span className={`w-4 text-right font-mono text-[11px] tabular-nums ${rank <= 3 ? "text-flare" : "text-bone-600"}`}>
        {rank}
      </span>
      <Image
        src={avatar(s.username)}
        alt={s.username}
        width={28}
        height={28}
        unoptimized
        className="h-7 w-7 rounded-full bg-ink-700 object-cover"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-bone-100">@{s.username}</span>
        <span className="flex items-center gap-1.5">
          <span
            className="rounded px-1 py-px font-mono text-[9px] font-semibold uppercase tracking-wide"
            style={{ color: TIER_COLOR[s.tier] ?? "#8b93a7", background: `${TIER_COLOR[s.tier] ?? "#8b93a7"}1a` }}
          >
            {s.tier}
          </span>
          <span className="font-mono text-[10px] text-bone-600">{fmtFollowers(s.followers)} followers</span>
        </span>
      </span>
      <span className="text-right">
        <span className="block font-mono text-sm font-bold tabular-nums text-bone-100">{s.signals}</span>
        <span className="block font-mono text-[9px] uppercase text-bone-600">signal{s.signals === 1 ? "" : "s"}</span>
      </span>
    </Link>
  );
}

// SVG buzz bars with avatar clusters on the loudest buckets + hover tooltip.
function BuzzBars({ series, window }: { series: BuzzBucket[]; window: "7d" | "30d" }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  const [tip, setTip] = useState<{ x: number; y: number; when: string; buzz: number; who: string[] } | null>(null);
  const H = 132;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((es) => setW(Math.max(280, es[0].contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (series.length === 0) return null;

  const iw = w - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const n = series.length;
  const bw = iw / n;
  const barW = Math.max(2, bw * 0.62);

  // peak buckets get avatar markers (top callers), like the reference "buzz" chart
  const peakIdx = series
    .map((s, i) => ({ i, buzz: s.buzz }))
    .sort((a, b) => b.buzz - a.buzz)
    .slice(0, 6)
    .filter((p) => p.buzz > 0)
    .map((p) => p.i);

  const fmtWhen = (iso: string) =>
    new Date(iso).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      ...(window === "7d" ? { hour: "numeric" } : {}),
      timeZone: "UTC",
    });

  return (
    <div ref={ref} className="relative w-full">
      <svg width={w} height={H} className="block">
        <defs>
          <linearGradient id="buzz-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={BUZZ_COLOR} stopOpacity="0.95" />
            <stop offset="100%" stopColor={BUZZ_COLOR} stopOpacity="0.35" />
          </linearGradient>
          <clipPath id="buzz-av">
            <circle r="8" />
          </clipPath>
        </defs>

        {series.map((s, i) => {
          const h = Math.max(1, (s.buzz / 100) * ih);
          const x = PAD.left + i * bw + (bw - barW) / 2;
          const y = PAD.top + ih - h;
          return (
            <rect
              key={i}
              x={x}
              y={y}
              width={barW}
              height={h}
              rx={Math.min(2, barW / 2)}
              fill="url(#buzz-fill)"
              opacity={tip && tip.x > x - 2 && tip.x < x + barW + 2 ? 1 : 0.82}
              onMouseEnter={() =>
                setTip({ x: x + barW / 2, y, when: fmtWhen(s.ts), buzz: s.buzz, who: s.topCallers })
              }
              onMouseLeave={() => setTip(null)}
            />
          );
        })}

        {/* avatar clusters on peak buckets */}
        {peakIdx.map((i) => {
          const s = series[i];
          const cx = PAD.left + i * bw + bw / 2;
          const h = (s.buzz / 100) * ih;
          const cy = PAD.top + ih - h - 10;
          return (
            <g key={`pk-${i}`} transform={`translate(${cx},${Math.max(PAD.top - 2, cy)})`}>
              <circle r="9.5" fill="#0a0a0a" stroke={BUZZ_COLOR} strokeWidth="1.25" />
              <image
                href={avatar(s.topCallers[0] || "x")}
                x="-8"
                y="-8"
                width="16"
                height="16"
                clipPath="url(#buzz-av)"
                preserveAspectRatio="xMidYMid slice"
              />
            </g>
          );
        })}
      </svg>

      {tip && (
        <div
          className="pointer-events-none absolute z-20 -translate-x-1/2 rounded-lg border border-white/10 bg-ink-900/95 px-2.5 py-1.5 shadow-xl backdrop-blur"
          style={{ left: Math.min(Math.max(tip.x, 60), w - 60), top: Math.max(0, tip.y - 56) }}
        >
          <div className="font-mono text-[10px] text-bone-500">{tip.when}</div>
          <div className="whitespace-nowrap text-xs font-semibold text-bone-100">
            buzz {tip.buzz.toFixed(0)}
          </div>
          {tip.who.length > 0 && (
            <div className="mt-0.5 max-w-[160px] truncate font-mono text-[10px] text-bone-400">
              @{tip.who.slice(0, 2).join(", @")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
