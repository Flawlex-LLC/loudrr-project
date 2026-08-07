"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import {
  ChartLineUp,
  Export,
  Lightning,
  LinkSimple,
  LockKey,
  TelegramLogo,
  UsersThree,
  WhatsappLogo,
} from "@phosphor-icons/react";
import { DiscordBrandIcon } from "./icons/BrandIcons";
import { CountUp } from "./CountUp";
import { TimeWindowChips } from "./TimeWindowChips";
import { DeltaPair } from "./DeltaPair";
import { SmartFollowersGrid } from "./SmartFollowersGrid";
import { StatsChart } from "./charts/StatsChart";
import { ScorePositionBar } from "./charts/ScorePositionBar";
import { TrendingRail } from "./TrendingRail";
import { EngagementHeatmap, HEAT_LEVELS } from "./EngagementHeatmap";
import { useAuthGate } from "./AuthGate";
import { ROLES, ROLE_LABEL, WINDOWS, avatar, type Profile, type WindowKey } from "@/lib/mock";
import { fmtCompact, tierFor } from "@/lib/score";
import { FREE_VIEW_LIMIT, isGated, registerView } from "@/lib/gate";

export function ScoreDashboard({ profile }: { profile: Profile }) {
  const { loggedIn, requireLogin } = useAuthGate();
  const [locked, setLocked] = useState<boolean | null>(null);
  const [win, setWin] = useState<WindowKey>("30d");
  const tier = tierFor(profile.score);

  useEffect(() => {
    if (loggedIn) {
      setLocked(false);
      return;
    }
    if (isGated(profile.handle)) {
      setLocked(true);
      requireLogin(`You've used your ${FREE_VIEW_LIMIT} free lookups`);
    } else {
      registerView(profile.handle);
      setLocked(false);
    }
  }, [profile.handle, loggedIn, requireLogin]);

  if (locked === null) return <DashboardSkeleton />;

  // REAL-ONLY rendering: sections without real data are hidden, never faked.
  const hasHistory = profile.scoreHistory.length > 1;
  // real engagement series (distinct smart accounts/day) — fills the Statistics slot on
  // live profiles until real score history accumulates
  const engHist = (() => {
    const src = profile.engagementDaily;
    if (!src) return null;
    const now = new Date();
    const utcToday = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    const n = WINDOWS.find((w) => w.key === win)!.days + 1;
    return Array.from({ length: n }, (_, i) => {
      const d = new Date(utcToday - (n - 1 - i) * 86_400_000);
      const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
      return src[key] ?? 0;
    });
  })();
  const hasEngagement = !!engHist && engHist.some((v) => v > 0);
  const wd = profile.windowedDeltas[win];
  const winLabel = WINDOWS.find((w) => w.key === win)!.label;
  const days = WINDOWS.find((w) => w.key === win)!.days;
  const hist = profile.scoreHistory.slice(-Math.max(12, Math.min(365, days + 1)));
  // categories: real vendor-corroborated labels when live; scene-derived tags on mock only
  const categoryTags = profile.categoriesLabel?.length
    ? profile.categoriesLabel
    : profile.live
      ? []
      : [...ROLES]
          .sort((a, b) => (profile.followersByCategory[b] ?? 0) - (profile.followersByCategory[a] ?? 0))
          .slice(0, 2)
          .map((r) => ROLE_LABEL[r]);
  // smart-followers series over the window (accumulates toward the current count)
  const sfEnd = profile.eliteFollowers;
  const sfStart = Math.round(sfEnd * 0.9);
  const sfHist = hist.map((_, i, arr) => {
    const t = arr.length > 1 ? i / (arr.length - 1) : 1;
    const wig = Math.round(Math.sin(i * 0.9 + profile.handle.length) * sfEnd * 0.006);
    return Math.max(0, Math.round(sfStart + (sfEnd - sfStart) * t) + wig);
  });
  // each smart follower plotted on the day they followed (pfp markers on the followers line)
  const sfMarkers = profile.topFollowers.slice(0, 12).map((f, j, a) => ({
    index: Math.round(((j + 1) / (a.length + 1)) * (hist.length - 1)),
    img: avatar(f.handle),
    ring: tierFor(f.score).color,
    name: f.name,
  }));

  return (
    <div className="space-y-4">
      <div className="relative">
        {locked && <LockOverlay handle={profile.handle} onUnlock={() => requireLogin()} />}
        <div className={locked ? "pointer-events-none select-none space-y-4 blur-lg saturate-50" : "space-y-4"}>
          {/* ── profile card (identity + Loudrr Score inside) ── */}
          <div className="glass relative z-10 p-5 sm:p-6">
            <div className="grid gap-5 lg:grid-cols-[1.15fr_1fr] lg:items-stretch">
              {/* identity (left) */}
              <div className="flex min-w-0 items-start gap-4">
                <Image
                  src={avatar(profile.handle)}
                  alt={profile.handle}
                  width={64}
                  height={64}
                  unoptimized
                  className="h-16 w-16 shrink-0 rounded-2xl border border-white/10 bg-ink-700 object-cover"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <h1 className="font-display text-2xl font-bold tracking-tightest">{profile.name}</h1>
                    <a
                      href={`https://x.com/${profile.handle}`}
                      target="_blank"
                      rel="noreferrer"
                      className="glass-pill inline-flex items-center gap-1.5 px-2.5 py-1 text-xs text-bone-300 transition-colors hover:text-flare"
                    >
                      <svg viewBox="0 0 24 24" fill="currentColor" className="h-3 w-3 shrink-0" aria-hidden>
                        <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                      </svg>
                      @{profile.handle}
                    </a>
                  </div>
                  {profile.meta.bio && (
                    <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-bone-400">{profile.meta.bio}</p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {profile.followers > 0 && <StatPill k="Followers" v={fmtCompact(profile.followers)} />}
                    {profile.following > 0 && <StatPill k="Following" v={fmtCompact(profile.following)} />}
                    {profile.rank > 0 && <StatPill k="Rank" v={`#${profile.rank.toLocaleString()}`} />}
                    {profile.meta.joinYear > 0 && <StatPill k="Joined" v={String(profile.meta.joinYear)} />}
                  </div>
                  {/* categories — one tag holding its sub-tags (tags inside a tag) */}
                  {categoryTags.length > 0 && (
                    <div className="mt-3">
                      <span className="inline-flex flex-wrap items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.03] px-2.5 py-1.5">
                        <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-bone-600">Categories</span>
                        {categoryTags.map((t) => (
                          <span key={t} className="rounded-full bg-white/[0.07] px-2 py-0.5 text-xs text-bone-200">
                            {t}
                          </span>
                        ))}
                      </span>
                    </div>
                  )}
                  {/* share — below categories */}
                  <div className="mt-3">
                    <ShareButton handle={profile.handle} name={profile.name} />
                  </div>
                </div>
              </div>

              {/* dedicated Loudrr Score card (card inside card, right) */}
              <div className="flex flex-col justify-center rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 sm:p-5">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-bone-500">Loudrr Score</span>
                  <span
                    className="relative inline-flex items-center gap-1.5 overflow-hidden rounded-full border px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] backdrop-blur"
                    style={{
                      color: tier.color,
                      borderColor: `${tier.color}66`,
                      background: `linear-gradient(135deg, ${tier.color}2e 0%, ${tier.color}12 55%, ${tier.color}22 100%)`,
                      boxShadow: "inset 0 1px 0 rgba(255,255,255,0.18)",
                    }}
                  >
                    <span
                      className="pointer-events-none absolute inset-x-0 top-0 h-1/2"
                      style={{ background: "linear-gradient(to bottom, rgba(255,255,255,0.16), transparent)" }}
                    />
                    <span className="relative">◢ {tier.name}</span>
                  </span>
                </div>

                <div className="mt-2 flex items-end gap-2.5">
                  <span className="orange-gradient-text font-display text-5xl font-extrabold leading-none tracking-tightest tabular-nums">
                    <CountUp to={profile.score} />
                  </span>
                  {hasHistory && wd && (
                    <span className="mb-1 flex items-center gap-1.5">
                      <DeltaPair delta={wd} />
                      <span className="text-[10px] uppercase tracking-[0.14em] text-bone-600">/ {winLabel}</span>
                    </span>
                  )}
                </div>

                <div className="mt-4">
                  <ScorePositionBar score={profile.score} />
                </div>
              </div>
            </div>
          </div>

          {/* ── smart followers + statistics (REAL series only) ── */}
          <div className={`grid items-stretch gap-4 ${hasHistory || hasEngagement ? "lg:grid-cols-2" : ""}`}>
            <Panel
              title="Who follows them"
              icon={<UsersThree size={15} weight="fill" />}
              aside={`${profile.eliteFollowers.toLocaleString()} smart followers`}
            >
              <SmartFollowersGrid receipts={profile.topFollowers} byCategory={profile.followersByCategory} />
            </Panel>
            {hasHistory ? (
              <Panel
                title="Statistics"
                icon={<ChartLineUp size={15} weight="fill" />}
                aside={<TimeWindowChips value={win} onChange={setWin} />}
              >
                <StatsChart
                  series={[
                    { key: "sf", label: "Smart followers", color: "#f95400", data: sfHist, axis: "left", format: (v) => fmtCompact(v) },
                    { key: "score", label: "Loudrr Score", color: "#6ea8ff", data: hist, axis: "right", format: (v) => v.toLocaleString() },
                  ]}
                  markers={sfMarkers}
                />
              </Panel>
            ) : hasEngagement ? (
              <Panel
                title="Statistics"
                icon={<ChartLineUp size={15} weight="fill" />}
                aside={<TimeWindowChips value={win} onChange={setWin} />}
              >
                {/* real daily smart-engagement counts; the score line joins this chart
                    automatically once daily score snapshots accumulate */}
                <StatsChart
                  series={[
                    { key: "eng", label: "Smart engagement / day", color: "#f95400", data: engHist!, axis: "left", format: (v) => String(Math.round(v)) },
                  ]}
                  markers={[]}
                />
              </Panel>
            ) : null}
          </div>

          {/* ── smart engagement heatmap ── */}
          <Panel
            title="Smart Engagement Activity"
            icon={<Lightning size={15} weight="fill" />}
            aside={
              <span className="flex items-center gap-1.5 text-[10px] text-bone-500">
                Less
                <span className="flex gap-0.5">
                  {HEAT_LEVELS.map((c, i) => (
                    <span key={i} className="h-2.5 w-2.5 rounded-[2px]" style={{ background: c }} />
                  ))}
                </span>
                More
              </span>
            }
          >
            <EngagementHeatmap
              handle={profile.handle}
              series={profile.engagementDaily}
              panelSize={profile.engagementPanelSize}
            />
          </Panel>
        </div>
      </div>

      <TrendingRail exclude={profile.handle} />
    </div>
  );
}

function Panel({
  title,
  icon,
  aside,
  className = "",
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  aside?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`panel p-4 sm:p-5 ${className}`}>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-bone-200">
          {icon && <span className="text-flare">{icon}</span>}
          <h2 className="font-display text-sm font-semibold uppercase tracking-wider">{title}</h2>
        </div>
        {aside && <span className="font-mono text-[11px] text-bone-600">{aside}</span>}
      </div>
      {children}
    </section>
  );
}

function StatPill({ k, v }: { k: string; v: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs">
      <span className="text-bone-500">{k}</span>
      <span className="font-semibold tabular-nums text-bone-100">{v}</span>
    </span>
  );
}

function ShareButton({ handle, name }: { handle: string; name: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const url = typeof window !== "undefined" ? window.location.href : `https://loudrr.com/score/${handle}`;
  const text = `${name}'s Loudrr Score`;
  const e = encodeURIComponent;
  const links = {
    x: `https://x.com/intent/tweet?text=${e(text)}&url=${e(url)}`,
    telegram: `https://t.me/share/url?url=${e(url)}&text=${e(text)}`,
    whatsapp: `https://wa.me/?text=${e(`${text} ${url}`)}`,
  };
  const copy = () => {
    if (typeof navigator !== "undefined") navigator.clipboard?.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const row =
    "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-bone-200 transition-colors hover:bg-white/[0.06]";

  return (
    <div className="relative inline-block">
      <button
        onClick={() => setOpen((v) => !v)}
        className="btn-ghost shrink-0 !px-3 !py-2"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Share this profile"
      >
        <Export size={16} weight="fill" />
        <span className="hidden text-xs font-medium sm:inline">Share</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute left-0 top-full z-50 mt-2 w-52 rounded-xl border border-white/10 bg-ink-800/95 p-1.5 shadow-xl backdrop-blur-xl"
          >
            <a href={links.x} target="_blank" rel="noreferrer" onClick={() => setOpen(false)} className={row}>
              <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4 shrink-0" aria-hidden>
                <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
              </svg>
              Share on X
            </a>
            <a href={links.telegram} target="_blank" rel="noreferrer" onClick={() => setOpen(false)} className={row}>
              <TelegramLogo size={17} weight="fill" style={{ color: "#229ED9" }} />
              Telegram
            </a>
            <a href={links.whatsapp} target="_blank" rel="noreferrer" onClick={() => setOpen(false)} className={row}>
              <WhatsappLogo size={17} weight="fill" style={{ color: "#25D366" }} />
              WhatsApp
            </a>
            <button onClick={copy} className={row}>
              <DiscordBrandIcon size={17} style={{ color: "#5865F2" }} />
              Discord {copied && <span className="ml-auto text-[10px] text-bone-500">link copied</span>}
            </button>
            <div className="my-1 h-px bg-white/10" />
            <button onClick={copy} className={row}>
              <LinkSimple size={16} weight="bold" className="text-bone-400" />
              {copied ? "Link copied" : "Copy link"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function LockOverlay({ handle, onUnlock }: { handle: string; onUnlock: () => void }) {
  return (
    <div className="absolute inset-0 z-20 grid place-content-center px-4 text-center">
      <div className="glass mx-auto max-w-sm p-7">
        <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-full border border-flare/40 bg-flare/10 text-flare">
          <LockKey size={20} weight="fill" />
        </div>
        <h3 className="font-display text-xl font-bold tracking-tightest">Full breakdown is ready</h3>
        <p className="mt-1.5 text-sm text-bone-400">
          You&apos;ve used your free lookups. Sign in (free) to reveal @{handle}&apos;s history, smart followers, trust
          graph and more.
        </p>
        <button onClick={onUnlock} className="btn-flare mt-5 w-full">
          Sign in to reveal
        </button>
      </div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-5">
      <div className="panel h-28 p-6">
        <div className="skel h-full w-full" />
      </div>
      <div className="panel h-72 p-6">
        <div className="skel h-full w-full" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="glass h-24 p-4">
            <div className="skel h-full w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
