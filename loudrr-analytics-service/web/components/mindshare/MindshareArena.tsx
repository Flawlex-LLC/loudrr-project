"use client";

import Image from "next/image";
import Link from "next/link";
import { useMemo, useState } from "react";
import { CaretDown, CaretUp } from "@phosphor-icons/react";
import {
  CRYPTO_COMPANIES,
  CRYPTO_VOICES,
  COMPANY_CATS,
  VOICE_CATS,
  VERTICALS,
  filterByCat,
  squarify,
  type MSItem,
  type Vertical,
} from "@/lib/mindshare";

type View = "voices" | "companies";
const WINDOWS = ["7D", "30D", "3M"] as const;
const PAGES = ["Top 20", "Top 21–50", "Top 51–100"] as const;

export function MindshareArena() {
  const [view, setView] = useState<View>("voices");
  const [vertical, setVertical] = useState<Vertical>("crypto");
  const [catV, setCatV] = useState("All");
  const [catC, setCatC] = useState("All");
  const [page, setPage] = useState(0);
  const [win, setWin] = useState<(typeof WINDOWS)[number]>("7D");
  const [metric, setMetric] = useState<"abs" | "rel">("abs");

  const cats = view === "voices" ? VOICE_CATS : COMPANY_CATS;
  const cat = view === "voices" ? catV : catC;
  const setCat = view === "voices" ? setCatV : setCatC;
  const useD30 = win !== "7D";

  const base = view === "voices" ? CRYPTO_VOICES : CRYPTO_COMPANIES;
  const filtered = useMemo(() => filterByCat(base, cat), [base, cat]);
  const pageSlice = filtered.slice(page * 20, page * 20 + 20);

  const gainers = useMemo(() => {
    const d = (i: MSItem) => (useD30 ? i.d30 : i.d7);
    return [...filtered]
      .sort((a, b) => (metric === "rel" ? d(b) / b.share - d(a) / a.share : d(b) - d(a)))
      .slice(0, 12);
  }, [filtered, metric, useD30]);

  return (
    <div className="mx-auto max-w-6xl">
      {/* header + view toggle */}
      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Share of voice</p>
          <h1 className="mt-1 font-display text-3xl font-extrabold tracking-tightest sm:text-4xl">Mindshare arena</h1>
        </div>
        <div className="seg">
          <button className="seg-item" data-active={view === "voices"} onClick={() => { setView("voices"); setPage(0); }}>
            Top Voices
          </button>
          <button className="seg-item" data-active={view === "companies"} onClick={() => { setView("companies"); setPage(0); }}>
            Top Companies
          </button>
        </div>
      </header>

      {/* vertical cards */}
      <div className="mb-5 grid grid-cols-3 gap-3">
        {VERTICALS.map((v) => {
          const active = v.key === vertical;
          return (
            <button
              key={v.key}
              onClick={() => v.live && setVertical(v.key)}
              disabled={!v.live}
              className={`group relative overflow-hidden rounded-xl border px-4 py-3.5 text-left transition-colors ${
                active ? "border-white/20 bg-white/[0.06]" : "border-white/[0.07] bg-ink-800/40 hover:bg-ink-800/70"
              } ${!v.live ? "cursor-not-allowed opacity-60" : ""}`}
            >
              <div className="flex items-center justify-between">
                <span className={`text-sm font-semibold ${active ? "text-bone-100" : "text-bone-300"}`}>
                  {v.label} {view === "voices" ? "Voices" : "Companies"}
                </span>
                {!v.live && (
                  <span className="rounded-full border border-white/10 px-1.5 py-0.5 font-mono text-[9px] uppercase text-bone-500">
                    soon
                  </span>
                )}
              </div>
              {active && <span className="absolute inset-x-0 bottom-0 h-[2px] bg-flare" />}
            </button>
          );
        })}
      </div>

      {/* category chips */}
      <div className="mb-5 flex flex-wrap gap-2">
        {cats.map((cc) => (
          <button
            key={cc}
            onClick={() => { setCat(cc); setPage(0); }}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              cc === cat
                ? "border-white/20 bg-white/[0.08] text-bone-100"
                : "border-white/[0.07] text-bone-500 hover:text-bone-200"
            }`}
          >
            {cc}
          </button>
        ))}
      </div>

      {vertical !== "crypto" ? (
        <div className="panel grid place-items-center px-6 py-24 text-center">
          <div className="font-display text-xl font-bold tracking-tightest text-bone-200">
            {VERTICALS.find((v) => v.key === vertical)?.label} mindshare is coming soon
          </div>
          <p className="mt-2 max-w-sm text-sm text-bone-500">Crypto is live now. AI and Stocks verticals are next.</p>
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[1.7fr_1fr]">
          {/* treemap */}
          <section className="panel p-3 sm:p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
              <div className="flex gap-1.5">
                {PAGES.map((p, i) => (
                  <button
                    key={p}
                    onClick={() => setPage(i)}
                    disabled={filtered.length <= i * 20}
                    className={`rounded-md px-2 py-1 font-mono text-[11px] transition-colors disabled:opacity-30 ${
                      page === i ? "bg-white/10 text-bone-100" : "text-bone-500 hover:text-bone-200"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded-md border border-white/10 px-2 py-1 font-mono text-[11px] text-bone-400">
                  All Languages
                </span>
                <div className="seg !p-0.5">
                  {WINDOWS.map((wkey) => (
                    <button key={wkey} className="seg-item !px-2 !py-1 !text-[11px]" data-active={win === wkey} onClick={() => setWin(wkey)}>
                      {wkey}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <Treemap items={pageSlice} useD30={useD30} />
          </section>

          {/* gainers */}
          <section className="panel p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-display text-sm font-semibold uppercase tracking-wider text-bone-200">Top movers</h2>
              <div className="seg !p-0.5">
                <button className="seg-item !px-2 !py-1 !text-[11px]" data-active={metric === "abs"} onClick={() => setMetric("abs")}>
                  Absolute
                </button>
                <button className="seg-item !px-2 !py-1 !text-[11px]" data-active={metric === "rel"} onClick={() => setMetric("rel")}>
                  Relative
                </button>
              </div>
            </div>
            <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 gap-y-1 px-1 pb-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-bone-600">
              <span>Name</span>
              <span className="text-right">Current</span>
              <span className="text-right">{useD30 ? "Δ30D" : "Δ7D"}</span>
            </div>
            <div className="flex flex-col">
              {gainers.map((g) => {
                const dv = useD30 ? g.d30 : g.d7;
                return (
                  <div key={g.id} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 rounded-lg px-1 py-1.5 transition-colors hover:bg-white/[0.04]">
                    <span className="flex min-w-0 items-center gap-2">
                      <Monogram item={g} size={22} />
                      <span className="min-w-0 truncate text-sm text-bone-200">{g.name}</span>
                    </span>
                    <span className="text-right font-mono text-xs tabular-nums text-bone-300">{g.share.toFixed(2)}%</span>
                    <span className={`inline-flex w-20 items-center justify-end gap-0.5 font-mono text-xs tabular-nums ${dv >= 0 ? "text-up" : "text-down"}`}>
                      {dv >= 0 ? <CaretUp size={10} weight="fill" /> : <CaretDown size={10} weight="fill" />}
                      {Math.abs(dv)}
                      {metric === "abs" ? "bps" : "%"}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

// monogram (companies) or avatar (voices)
function Monogram({ item, size }: { item: MSItem; size: number }) {
  if (item.img) {
    return (
      <Image src={item.img} alt={item.name} width={size} height={size} unoptimized className="shrink-0 rounded-full bg-ink-700 object-cover" style={{ width: size, height: size }} />
    );
  }
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full font-mono font-bold text-white"
      style={{ width: size, height: size, background: item.color, fontSize: size * 0.4 }}
    >
      {item.sub.slice(0, 1)}
    </span>
  );
}

function Treemap({ items, useD30 }: { items: MSItem[]; useD30: boolean }) {
  const W = 120;
  const H = 80;
  const rects = squarify(items.map((i) => i.share), W, H);
  return (
    <div className="relative w-full overflow-hidden rounded-lg" style={{ aspectRatio: `${W} / ${H}` }}>
      {items.map((it, i) => {
        const r = rects[i];
        if (!r) return null;
        const dv = useD30 ? it.d30 : it.d7;
        const up = dv >= 0;
        const areaPct = (r.w / W) * (r.h / H);
        const big = areaPct > 0.06;
        const mid = areaPct > 0.022;
        return (
          <Link
            key={it.id}
            href={it.img ? `/score/${it.id}` : "#"}
            className="group absolute overflow-hidden rounded-md border p-2 transition-[filter] hover:brightness-110"
            style={{
              left: `${(r.x / W) * 100}%`,
              top: `${(r.y / H) * 100}%`,
              width: `${(r.w / W) * 100}%`,
              height: `${(r.h / H) * 100}%`,
              background: up
                ? "linear-gradient(155deg, rgba(34,110,84,0.55), rgba(18,56,45,0.5))"
                : "linear-gradient(155deg, rgba(120,48,52,0.55), rgba(66,26,30,0.5))",
              borderColor: up ? "rgba(69,212,159,0.32)" : "rgba(255,93,82,0.32)",
            }}
          >
            <div className="flex items-start justify-between gap-1">
              <span className={`min-w-0 truncate font-semibold text-white ${big ? "text-base" : mid ? "text-sm" : "text-[11px]"}`}>
                {it.sub.replace(/^@/, "")}
              </span>
              {(big || mid) && <span className="shrink-0 font-mono text-[10px] text-white/50">{items.indexOf(it) + 1}</span>}
            </div>
            {(big || mid) && (
              <div className={`mt-0.5 font-mono tabular-nums text-white/85 ${big ? "text-lg" : "text-xs"}`}>
                {it.share.toFixed(2)}%
              </div>
            )}
          </Link>
        );
      })}
    </div>
  );
}
