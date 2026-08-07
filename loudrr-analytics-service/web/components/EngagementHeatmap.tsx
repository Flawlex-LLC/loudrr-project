"use client";

import { useMemo, useState } from "react";
import { createPortal } from "react-dom";

// GitHub-style contribution heatmap of "smart engagement" over the last ~12 months.
// Counts only, no coverage annotations (founder call 2026-07-07): the historical backfill
// keeps the panel's fetched windows at the full heatmap year, so per-day caveats are noise.
export const HEAT_LEVELS = [
  "rgba(255,255,255,0.05)",
  "rgba(249,84,0,0.28)",
  "rgba(249,84,0,0.5)",
  "rgba(249,84,0,0.72)",
  "#f95400",
];

const WEEKS = 52;
const TOTAL = WEEKS * 7;
const MONTH_FMT = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % 100000;
}

function countAt(seed: number, i: number): number {
  const x = Math.sin(seed + i * 12.9898) * 43758.5453;
  const r = x - Math.floor(x); // 0..1
  const recent = i / TOTAL; // 0 old → 1 recent
  const base = r * (0.35 + recent * 1.55);
  return Math.max(0, Math.round((base - 0.28) * 22));
}

function level(c: number): number {
  if (c <= 0) return 0;
  if (c < 5) return 1;
  if (c < 10) return 2;
  if (c < 18) return 3;
  return 4;
}

// The API buckets by UTC days (EngEdge.day) — the grid must be anchored to UTC too, or
// non-UTC viewers see every count shifted a cell and the newest UTC day invisible.
function dateForIndex(index: number): Date {
  const now = new Date();
  const utcToday = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return new Date(utcToday - (TOTAL - 1 - index) * 86_400_000);
}

function isoKey(d: Date): string {
  // UTC-date ISO key (YYYY-MM-DD) — matches the API's sparse `counts` map keys exactly
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${d.getUTCFullYear()}-${m}-${day}`;
}

// Real data thresholds — distinct smart engagers/day are small integers (mock runs hotter).
function liveLevel(c: number): number {
  if (c <= 0) return 0;
  if (c < 3) return 1;
  if (c < 6) return 2;
  if (c < 12) return 3;
  return 4;
}

export function EngagementHeatmap({
  handle,
  series,
  panelSize,
}: {
  handle: string;
  /** Sparse date-keyed counts from /v1/smart-engagement; absent -> deterministic mock (dev). */
  series?: Record<string, number>;
  /** Tracked smart accounts in the panel. */
  panelSize?: number;
}) {
  const seed = hash(handle);
  const live = !!series;
  const [tip, setTip] = useState<{ left: number; top: number; date: string; count: number } | null>(null);

  // Precompute per-cell dates/counts once per (handle, series) — the component re-renders
  // on every tooltip move, and 364 Date allocations per hover would be wasted work.
  const { counts, labels, months } = useMemo(() => {
    const dates = Array.from({ length: TOTAL }, (_, i) => dateForIndex(i));
    const counts = dates.map((d, i) =>
      live ? (series![isoKey(d)] ?? 0) : countAt(seed, i));
    const labels = dates.map((d) =>
      d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }));
    // month axis derived from the actual grid dates (a hardcoded list is only right in July)
    const months = Array.from({ length: 12 }, (_, i) =>
      MONTH_FMT.format(dates[Math.floor((i * TOTAL) / 12)]));
    return { counts, labels, months };
  }, [seed, series, live]);

  const levelFor = (c: number) => (live ? liveLevel(c) : level(c));

  const enter = (e: React.MouseEvent, index: number) => {
    const r = e.currentTarget.getBoundingClientRect();
    setTip({
      left: r.left + r.width / 2,
      top: r.top,
      date: labels[index],
      count: counts[index],
    });
  };

  return (
    <div className="w-full overflow-x-auto pb-1">
      <div className="min-w-[680px]">
        <div
          className="grid w-full gap-[4px]"
          style={{
            gridTemplateColumns: `repeat(${WEEKS}, minmax(0, 1fr))`,
            gridTemplateRows: "repeat(7, minmax(0, 1fr))",
            gridAutoFlow: "column",
          }}
        >
          {counts.map((c, index) => (
            <div
              key={index}
              className="aspect-square rounded-[3px] transition-[outline] hover:outline hover:outline-1 hover:outline-white/40"
              style={{ background: HEAT_LEVELS[levelFor(c)] }}
              onMouseEnter={(e) => enter(e, index)}
              onMouseLeave={() => setTip(null)}
            />
          ))}
        </div>
        <div className="relative mt-2 h-4 w-full">
          {months.map((m, i) => (
            <span
              key={i}
              className="absolute font-mono text-[11px] text-bone-600"
              style={{ left: `${(i / months.length) * 100}%` }}
            >
              {m}
            </span>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-bone-600">
          Replies, retweets &amp; quotes from{" "}
          {live && panelSize ? `${panelSize.toLocaleString()} ` : ""}tracked smart accounts.
        </p>
      </div>

      {tip &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            className="pointer-events-none fixed z-[9999] -translate-x-1/2 -translate-y-full"
            style={{ left: tip.left, top: tip.top - 10 }}
          >
            <div className="rounded-lg border border-white/10 bg-ink-900/95 px-3 py-2 shadow-2xl backdrop-blur">
              <div className="font-mono text-[11px] text-bone-400">{tip.date}</div>
              <div className="mt-0.5 whitespace-nowrap text-sm">
                <span className="font-semibold tabular-nums text-bone-100">{tip.count}</span>{" "}
                <span className="text-bone-400">smart engagement{tip.count === 1 ? "" : "s"}</span>
              </div>
            </div>
            <div className="mx-auto -mt-[5px] h-2.5 w-2.5 rotate-45 border-b border-r border-white/10 bg-ink-900/95" />
          </div>,
          document.body
        )}
    </div>
  );
}
