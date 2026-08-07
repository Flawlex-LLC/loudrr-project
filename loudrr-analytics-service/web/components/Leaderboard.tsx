"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, CaretDown, CaretUp, LockSimple, Rows, SquaresFour } from "@phosphor-icons/react";
import { Sparkline } from "./charts/Sparkline";
import { RankMovement } from "./RankMovement";
import { DeltaPair } from "./DeltaPair";
import { TimeWindowChips } from "./TimeWindowChips";
import { avatar, CATEGORIES, LEADERBOARD, WINDOWS, type Profile, type WindowKey } from "@/lib/mock";
import { getLeaderboard, USE_LIVE, type LeaderboardItem } from "@/lib/api";
import { fmtCompact, tierFor } from "@/lib/score";

type SortKey = "rank" | "score" | "delta" | "sf" | "ratio";

export function Leaderboard() {
  // REAL-ONLY when an API is configured — the mock board below exists purely for
  // offline design work and never renders in a live deployment.
  if (USE_LIVE) return <LiveBoard />;
  return <MockBoard />;
}

type LiveSort = "rank" | "score" | "sf" | "followers" | "ratio";

function LiveBoard() {
  const [items, setItems] = useState<LeaderboardItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [sort, setSort] = useState<{ key: LiveSort; dir: "asc" | "desc" }>({ key: "rank", dir: "asc" });

  useEffect(() => {
    let dead = false;
    getLeaderboard(100).then((r) => {
      if (dead) return;
      setItems(r?.items ?? []);
      setTotal(r?.total ?? 0);
    });
    return () => {
      dead = true;
    };
  }, []);

  const rows = useMemo(() => {
    if (!items) return null;
    const dir = sort.dir === "asc" ? 1 : -1;
    const ratio = (p: LeaderboardItem) =>
      p.followers ? (p.eliteFollowers ?? 0) / p.followers : 0;
    return [...items].sort((a, b) => {
      switch (sort.key) {
        case "score": return (a.score - b.score) * dir;
        case "sf": return ((a.eliteFollowers ?? 0) - (b.eliteFollowers ?? 0)) * dir;
        case "followers": return ((a.followers ?? 0) - (b.followers ?? 0)) * dir;
        case "ratio": return (ratio(a) - ratio(b)) * dir;
        default: return (a.rank - b.rank) * dir;
      }
    });
  }, [items, sort]);

  const toggle = (key: LiveSort) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "rank" ? "asc" : "desc" }));

  const Head = ({ label, k, className = "" }: { label: string; k: LiveSort; className?: string }) => {
    const active = sort.key === k;
    return (
      <button onClick={() => toggle(k)} aria-pressed={active}
              className={`-my-2 flex items-center gap-1 py-2 uppercase tracking-[0.14em] transition-colors hover:text-bone-300 ${active ? "text-flare" : ""} ${className}`}>
        {label}
        {active && (sort.dir === "asc" ? <CaretUp size={10} weight="bold" /> : <CaretDown size={10} weight="bold" />)}
      </button>
    );
  };

  const grid = "grid grid-cols-[34px_1fr_auto] items-center gap-3 sm:grid-cols-[40px_1fr_96px_96px] lg:grid-cols-[48px_1fr_110px_110px_84px_96px]";

  return (
    <div>
      <div className="panel mt-2 overflow-hidden">
        <div className={`${grid} border-b border-white/[0.07] px-4 py-3 font-mono text-[10px] uppercase tracking-[0.14em] text-bone-600`}>
          <Head label="#" k="rank" />
          <span>Account</span>
          <Head label="Smart flw" k="sf" className="hidden justify-end sm:flex" />
          <Head label="Followers" k="followers" className="hidden justify-end sm:flex" />
          <Head label="Ratio" k="ratio" className="hidden justify-end lg:flex" />
          <Head label="Score" k="score" className="justify-end" />
        </div>
        <ul>
          {rows === null
            ? Array.from({ length: 12 }).map((_, i) => (
                <li key={i} className={`${grid} border-b border-white/[0.07] px-4 py-3`}>
                  <div className="skel h-4 w-4" />
                  <div className="skel h-7 w-40" />
                  <div className="skel hidden h-4 w-full sm:block" />
                  <div className="skel hidden h-4 w-full sm:block" />
                  <div className="skel hidden h-4 w-full lg:block" />
                  <div className="skel h-4 w-full" />
                </li>
              ))
            : rows.map((p) => {
                const tier = tierFor(p.score);
                const ratio = p.followers ? ((p.eliteFollowers ?? 0) / p.followers) * 100 : null;
                return (
                  <li key={p.userName}>
                    <Link href={`/score/${p.userName}`}
                          className={`${grid} border-b border-white/[0.07] px-4 py-3 transition-colors hover:bg-white/[0.03]`}>
                      <span className="font-mono text-sm tabular-nums text-bone-300">{p.rank}</span>
                      <span className="flex min-w-0 items-center gap-3">
                        <Image src={avatar(p.userName)} alt={p.userName} width={36} height={36} unoptimized
                               className="h-9 w-9 shrink-0 rounded-full bg-ink-700 object-cover" />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium text-bone-100">{p.name || p.userName}</span>
                          <span className="block truncate font-mono text-xs text-bone-500">@{p.userName}</span>
                        </span>
                      </span>
                      <span className="hidden text-right font-mono text-sm tabular-nums text-bone-300 sm:block">
                        {p.eliteFollowers != null ? fmtCompact(p.eliteFollowers) : "—"}
                      </span>
                      <span className="hidden text-right font-mono text-sm tabular-nums text-bone-400 sm:block">
                        {p.followers != null ? fmtCompact(p.followers) : "—"}
                      </span>
                      <span className="hidden text-right font-mono text-sm tabular-nums lg:block">
                        {ratio != null ? (
                          <span className={ratio >= 5 ? "text-up" : "text-bone-400"}>{ratio.toFixed(1)}%</span>
                        ) : "—"}
                      </span>
                      <span className="text-right font-mono text-lg font-bold tabular-nums text-bone-100"
                            aria-label={`${p.score.toLocaleString()} score, ${tier.name} tier`}>
                        {p.score.toLocaleString()}
                      </span>
                    </Link>
                  </li>
                );
              })}
        </ul>
      </div>
      {total > 0 && (
        <p className="mt-3 text-center font-mono text-[11px] text-bone-600">
          Top {rows?.length ?? 0} of {total.toLocaleString()} ranked accounts on crypto X
        </p>
      )}
    </div>
  );
}

function MockBoard() {
  const cat = "crypto";
  const [win, setWin] = useState<WindowKey>("7d");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "rank", dir: "asc" });
  const [dense, setDense] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    try {
      const d = localStorage.getItem("loudrr.lb.dense");
      if (d) setDense(d === "1");
      const w = localStorage.getItem("loudrr.lb.win") as WindowKey | null;
      if (w && WINDOWS.some((x) => x.key === w)) setWin(w);
    } catch {}
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("loudrr.lb.dense", dense ? "1" : "0");
      localStorage.setItem("loudrr.lb.win", win);
    } catch {}
  }, [dense, win]);

  const live = CATEGORIES.find((c) => c.key === cat)?.live;
  const days = WINDOWS.find((w) => w.key === win)!.days;
  const sparkLen = Math.max(12, Math.min(90, days + 1));

  const rows = useMemo(() => {
    const r = [...LEADERBOARD];
    const dir = sort.dir === "asc" ? 1 : -1;
    r.sort((a, b) => {
      switch (sort.key) {
        case "score":
          return (a.score - b.score) * dir;
        case "delta":
          return (a.windowedDeltas[win].relPct - b.windowedDeltas[win].relPct) * dir;
        case "sf":
          return (a.eliteFollowers - b.eliteFollowers) * dir;
        case "ratio":
          return (a.smartFollowerRatio - b.smartFollowerRatio) * dir;
        default:
          return (a.rank - b.rank) * dir;
      }
    });
    return r;
  }, [sort, win]);

  // shared sparkline domain so slopes are comparable across rows
  const [lo, hi] = useMemo(() => {
    let mn = Infinity;
    let mx = -Infinity;
    rows.forEach((p) => {
      const s = p.scoreHistory.slice(-sparkLen);
      mn = Math.min(mn, ...s);
      mx = Math.max(mx, ...s);
    });
    return isFinite(mn) && isFinite(mx) ? [mn, mx] : [0, 1];
  }, [rows, sparkLen]);

  const movers = useMemo(() => [...LEADERBOARD].sort((a, b) => b.windowedDeltas[win].relPct - a.windowedDeltas[win].relPct), [win]);

  const toggleSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "rank" ? "asc" : "desc" }));

  return (
    <div>
      {/* controls */}
      <div className="flex flex-wrap items-center justify-end gap-3">
        {live && (
          <div className="flex items-center gap-2">
            <TimeWindowChips value={win} onChange={setWin} />
            <button
              onClick={() => setDense((d) => !d)}
              className="btn-ghost !px-3 !py-2"
              title={dense ? "Comfortable density" : "Compact density"}
              aria-label={dense ? "Switch to comfortable density" : "Switch to compact density"}
            >
              {dense ? <SquaresFour size={16} weight="fill" /> : <Rows size={16} weight="fill" />}
            </button>
          </div>
        )}
      </div>

      {!live ? (
        <ComingSoon label={CATEGORIES.find((c) => c.key === cat)?.label ?? ""} />
      ) : (
        <>
          {/* movers */}
          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            <MoverCard title={`Gainers · ${WINDOWS.find((w) => w.key === win)!.label}`} rows={movers.slice(0, 3)} win={win} />
            <MoverCard title={`Fading · ${WINDOWS.find((w) => w.key === win)!.label}`} rows={movers.slice(-3).reverse()} win={win} />
          </div>

          {/* table */}
          <div className="panel mt-6 overflow-hidden">
            {/* header */}
            <div
              className={`grid grid-cols-[34px_1fr_auto] items-center gap-3 border-b border-white/[0.07] px-4 py-3 font-mono text-[10px] uppercase tracking-[0.14em] text-bone-600 sm:grid-cols-[40px_1fr_110px_96px] lg:grid-cols-[48px_1fr_110px_84px_76px_120px_96px]`}
            >
              <Sortable label="#" k="rank" sort={sort} onClick={toggleSort} />
              <span>Account</span>
              <span className="hidden text-right lg:block">Trend</span>
              <Sortable className="hidden justify-end lg:flex" label="SF" k="sf" sort={sort} onClick={toggleSort} align="right" />
              <Sortable className="hidden justify-end lg:flex" label="Ratio" k="ratio" sort={sort} onClick={toggleSort} align="right" />
              <Sortable className="hidden justify-end sm:flex" label={WINDOWS.find((w) => w.key === win)!.label} k="delta" sort={sort} onClick={toggleSort} align="right" />
              <Sortable label="Score" k="score" sort={sort} onClick={toggleSort} align="right" />
            </div>

            {/* rows */}
            <ul>
              {!mounted
                ? Array.from({ length: 10 }).map((_, i) => (
                    <li key={i} className="grid grid-cols-[34px_1fr_auto] gap-3 border-b border-white/[0.07] px-4 py-3 sm:grid-cols-[40px_1fr_110px_96px] lg:grid-cols-[48px_1fr_110px_84px_76px_120px_96px]">
                      <div className="skel h-4 w-4" />
                      <div className="skel h-7 w-40" />
                      <div className="skel hidden h-4 w-full lg:block" />
                      <div className="skel hidden h-4 w-full lg:block" />
                      <div className="skel hidden h-4 w-full lg:block" />
                      <div className="skel hidden h-4 w-full sm:block" />
                      <div className="skel h-4 w-full" />
                    </li>
                  ))
                : rows.map((p) => {
                    const tier = tierFor(p.score);
                    return (
                      <li key={p.handle}>
                        <Link
                          href={`/score/${p.handle}`}
                          className={`grid grid-cols-[34px_1fr_auto] items-center gap-3 border-b border-white/[0.07] px-4 transition-colors hover:bg-white/[0.03] sm:grid-cols-[40px_1fr_110px_96px] lg:grid-cols-[48px_1fr_110px_84px_76px_120px_96px] ${
                            dense ? "py-2" : "py-3"
                          }`}
                        >
                          {/* rank + movement */}
                          <span className="flex flex-col items-start">
                            <span className="font-mono text-sm tabular-nums text-bone-300">{p.rank}</span>
                            <span className="lg:hidden">
                              <RankMovement rank={0} delta={p.rankByWindow[win].delta} showRank={false} />
                            </span>
                          </span>
                          {/* account */}
                          <span className="flex min-w-0 items-center gap-3">
                            <Image
                              src={avatar(p.handle)}
                              alt={p.handle}
                              width={dense ? 28 : 36}
                              height={dense ? 28 : 36}
                              unoptimized
                              className={`shrink-0 rounded-full bg-ink-700 object-cover ${dense ? "h-7 w-7" : "h-9 w-9"}`}
                            />
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-medium text-bone-100">{p.name}</span>
                              <span className="block truncate font-mono text-xs text-bone-500">@{p.handle}</span>
                            </span>
                          </span>
                          {/* sparkline */}
                          <span className="hidden justify-self-end lg:block">
                            <Sparkline data={p.scoreHistory.slice(-sparkLen)} width={102} height={dense ? 24 : 32} min={lo} max={hi} color="auto" dot={false} />
                          </span>
                          {/* SF */}
                          <span className="hidden text-right font-mono text-sm tabular-nums text-bone-300 lg:block">
                            {fmtCompact(p.eliteFollowers)}
                          </span>
                          {/* ratio */}
                          <span className="hidden text-right font-mono text-sm tabular-nums lg:block">
                            <span className={p.smartFollowerRatio >= 0.1 ? "text-up" : "text-bone-400"}>
                              {(p.smartFollowerRatio * 100).toFixed(1)}%
                            </span>
                          </span>
                          {/* window delta */}
                          <span className="hidden justify-self-end sm:block">
                            <DeltaPair delta={p.windowedDeltas[win]} size="sm" />
                          </span>
                          {/* score */}
                          <span
                            className="text-right font-mono text-lg font-bold tabular-nums text-bone-100"
                            aria-label={`${p.score.toLocaleString()} score, ${tier.name} tier`}
                          >
                            {p.score.toLocaleString()}
                          </span>
                        </Link>
                      </li>
                    );
                  })}
            </ul>
          </div>
          <p className="mt-3 text-center font-mono text-[11px] text-bone-600">
            Showing the top {rows.length} accounts on crypto X
          </p>
        </>
      )}
    </div>
  );
}

function Sortable({
  label,
  k,
  sort,
  onClick,
  align = "left",
  className = "",
}: {
  label: string;
  k: SortKey;
  sort: { key: SortKey; dir: "asc" | "desc" };
  onClick: (k: SortKey) => void;
  align?: "left" | "right";
  className?: string;
}) {
  const active = sort.key === k;
  return (
    <button
      onClick={() => onClick(k)}
      aria-pressed={active}
      aria-label={`Sort by ${k === "delta" ? "change" : k === "sf" ? "smart followers" : k === "ratio" ? "smart-follower ratio" : k}`}
      className={`-my-2 flex items-center gap-1 py-2 uppercase tracking-[0.14em] transition-colors hover:text-bone-300 ${
        active ? "text-flare" : ""
      } ${align === "right" ? "justify-end" : ""} ${className}`}
    >
      {label}
      {active && (sort.dir === "asc" ? <CaretUp size={10} weight="bold" /> : <CaretDown size={10} weight="bold" />)}
    </button>
  );
}

function MoverCard({ title, rows, win }: { title: string; rows: Profile[]; win: WindowKey }) {
  const days = WINDOWS.find((w) => w.key === win)!.days;
  const sparkLen = Math.max(12, Math.min(90, days + 1));
  return (
    <div className="panel p-4">
      <div className="eyebrow mb-3">{title}</div>
      <div className="space-y-1">
        {rows.map((p) => {
          const up = p.windowedDeltas[win].relPct >= 0;
          return (
            <Link key={p.handle} href={`/score/${p.handle}`} className="flex items-center gap-3 rounded-lg p-1.5 transition-colors hover:bg-white/5">
              <Image src={avatar(p.handle)} alt={p.handle} width={30} height={30} unoptimized className="h-[30px] w-[30px] rounded-full bg-ink-700 object-cover" />
              <span className="min-w-0 flex-1 truncate font-mono text-sm text-bone-200">@{p.handle}</span>
              <Sparkline data={p.scoreHistory.slice(-sparkLen)} width={56} height={20} color="auto" dot={false} />
              <span className={`flex w-16 items-center justify-end gap-0.5 font-mono text-sm tabular-nums ${up ? "text-up" : "text-down"}`}>
                {up ? <ArrowUp size={13} weight="bold" /> : <ArrowDown size={13} weight="bold" />}
                {Math.abs(p.windowedDeltas[win].relPct).toFixed(1)}%
              </span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function ComingSoon({ label }: { label: string }) {
  return (
    <div className="panel mt-6 grid place-items-center gap-3 px-6 py-20 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-full border border-white/10 text-bone-500">
        <LockSimple size={20} weight="fill" />
      </div>
      <h3 className="font-display text-2xl font-bold tracking-tightest">{label} leaderboard is brewing</h3>
      <p className="max-w-sm text-sm text-bone-500">
        Each vertical takes time to score properly. Crypto&apos;s live — {label} is next.
      </p>
      <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-bone-600">Join the waitlist for early access</span>
    </div>
  );
}
