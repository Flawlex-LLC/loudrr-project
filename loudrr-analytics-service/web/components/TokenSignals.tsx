"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowSquareOut, CheckCircle, Circle, Copy, Megaphone, Wallet } from "@phosphor-icons/react";
import { CandleChart, CALLS_COLOR, RIDES_COLOR } from "@/components/charts/CandleChart";
import { SocialBuzz } from "@/components/SocialBuzz";
import { LiveTape } from "@/components/LiveTape";
import { useLiveToken, type LiveCall, type LiveCandle, type LiveRide, type LiveTrade } from "@/lib/live";
import {
  getKolChart,
  getKolToken,
  type ChartTimeframe,
  type KolChart,
  type KolToken,
  type KolTokenCall,
} from "@/lib/api";

const TIMEFRAMES: ChartTimeframe[] = ["5m", "15m", "1h", "4h", "1d"];
import { avatar } from "@/lib/mock";

// Dedicated token analytics — the Bitget "coin page": header + big call chart with KOL pins,
// per-window call stats, the "KOL signal" leaderboard (who calls this the most), and the
// full call feed linking to the actual tweets.

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toPrecision(3)}`;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso + "Z").getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function hoursAgo(iso: string | null): number {
  if (!iso) return Infinity;
  return (Date.now() - new Date(iso + "Z").getTime()) / 3_600_000;
}

export function TokenSignals({ contract }: { contract: string }) {
  const [token, setToken] = useState<KolToken | null>(null);
  const [calls, setCalls] = useState<KolTokenCall[] | null>(null);
  const [chart, setChart] = useState<KolChart | null>(null);
  const [copied, setCopied] = useState(false);
  // chart settings: each pin series can be toggled independently (Bitget-style)
  const [showCalls, setShowCalls] = useState(true);
  const [showRides, setShowRides] = useState(true);
  const [tf, setTf] = useState<ChartTimeframe>("4h");

  // token + calls: fetch once per contract
  useEffect(() => {
    let dead = false;
    getKolToken(contract).then((t) => {
      if (dead) return;
      setToken(t.token);
      setCalls(t.calls);
    });
    return () => {
      dead = true;
    };
  }, [contract]);

  // chart: refetch when the timeframe chip changes (keeps pins, swaps candle granularity)
  useEffect(() => {
    let dead = false;
    setChart(null);
    getKolChart(contract, tf).then((c) => {
      if (!dead) setChart(c);
    });
    return () => {
      dead = true;
    };
  }, [contract, tf]);

  // ── realtime ──────────────────────────────────────────────────────────────
  // ONE socket for the whole page (chart + tape + calls). The server polls the free feed
  // once per token and fans out, so opening this page adds no per-viewer feed cost.
  const [liveTrades, setLiveTrades] = useState<LiveTrade[]>([]);
  const [buzzBump, setBuzzBump] = useState(0);

  const onCandle = useCallback((c: LiveCandle) => {
    setChart((prev) => {
      if (!prev) return prev;
      const candles = [...prev.candles];
      const last = candles[candles.length - 1];
      if (!last || c.ts > last.ts) candles.push(c);
      else if (c.ts === last.ts) candles[candles.length - 1] = c;  // bar still forming
      else return prev;                                            // stale frame, ignore
      return { ...prev, candles };
    });
  }, []);

  const onCall = useCallback((c: LiveCall) => {
    // a tracked KOL just posted about this token — pin it on the chart, top the feed, and
    // let Social Buzz recompute (its bars and signal board both move on a new call)
    const call: KolTokenCall = { ...c, confidence: c.confidence ?? "live" };
    setBuzzBump((n) => n + 1);
    setCalls((prev) =>
      prev && prev.some((p) => p.tweetId === call.tweetId) ? prev : [call, ...(prev ?? [])],
    );
    setChart((prev) =>
      prev && !prev.calls.some((p) => p.tweetId === call.tweetId)
        ? { ...prev, calls: [...prev.calls, call] }
        : prev,
    );
  }, []);

  const onTrades = useCallback((t: LiveTrade[]) => {
    setLiveTrades(t);
  }, []);

  const onRide = useCallback((r: LiveRide) => {
    // a tracked KOL wallet just traded this token onchain — pin it on the chart's ride layer
    // and top the rides feed. dedup by (wallet, ts) since a chart refetch may re-list it.
    setChart((prev) => {
      if (!prev) return prev;
      const dup = prev.rides.some(
        (x) => x.username === r.username && x.ts === r.ts && x.side === r.side,
      );
      return dup ? prev : { ...prev, rides: [...prev.rides, r] };
    });
  }, []);

  const { live } = useLiveToken(contract, tf, { onCandle, onCall, onTrades, onRide });

  const copyCa = () => {
    navigator.clipboard?.writeText(contract).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };

  // initial load gates on token+calls only; the chart re-loads independently on tf change
  if (calls === null) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-20 w-full rounded-2xl" />
        <div className="skeleton h-[260px] w-full rounded-2xl" />
        <div className="skeleton h-40 w-full rounded-2xl" />
      </div>
    );
  }

  if (token === null) {
    return (
      <div className="panel flex flex-col items-center gap-2 px-6 py-14 text-center">
        <Megaphone size={26} className="text-bone-600" />
        <p className="font-medium text-bone-300">Token not tracked yet</p>
        <p className="max-w-sm text-sm text-bone-500">
          It shows up here once a tracked smart account calls it.
        </p>
        <Link href="/kol-signals" className="btn-ghost mt-2">
          <ArrowLeft size={14} /> Back to KOL Signals
        </Link>
      </div>
    );
  }

  const chg = token.change24h;
  const c24 = calls.filter((c) => hoursAgo(c.ts) <= 24).length;
  const c7d = calls.filter((c) => hoursAgo(c.ts) <= 24 * 7).length;

  // distinct callers, for the header "KOLs" stat (the ranked board lives in <SocialBuzz>)
  const kolCount = new Set(calls.map((c) => c.username).filter(Boolean)).size;

  return (
    <div className="space-y-4">
      <Link href="/kol-signals" className="inline-flex items-center gap-1.5 text-sm text-bone-500 transition-colors hover:text-bone-200">
        <ArrowLeft size={14} /> KOL Signals
      </Link>

      {/* ── token header ── */}
      <div className="glass relative z-10 p-5">
        <div className="flex flex-wrap items-center gap-4">
          {token.image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={token.image} alt="" className="h-12 w-12 rounded-full bg-ink-700 object-cover" />
          ) : (
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-ink-700 font-mono text-sm text-bone-500">
              {(token.symbol || "?").slice(0, 3)}
            </span>
          )}
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="font-display text-2xl font-bold tracking-tightest">{token.symbol || "???"}</h1>
              {token.chain && (
                <span className="rounded-full border border-white/10 px-2 py-0.5 font-mono text-[10px] uppercase text-bone-500">
                  {token.chain}
                </span>
              )}
            </div>
            <p className="truncate text-sm text-bone-500">{token.name}</p>
          </div>
          <div className="ml-auto flex items-end gap-6">
            <Stat k="Price" v={token.priceUsd != null ? fmtMoney(token.priceUsd) : "—"}
                  sub={chg != null ? `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}% 24h` : undefined}
                  subUp={chg != null ? chg >= 0 : undefined} />
            <Stat k="Market cap" v={fmtMoney(token.mcapUsd)} />
            <Stat k="Calls 24h" v={String(c24)} accent />
            <Stat k="Calls 7d" v={String(c7d)} />
            <Stat k="KOLs" v={String(kolCount)} />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button onClick={copyCa}
                  className="flex items-center gap-1.5 rounded-full border border-white/[0.08] px-2.5 py-1 font-code text-[11px] text-bone-500 transition-colors hover:text-bone-200">
            {copied ? <CheckCircle size={12} /> : <Copy size={12} />}
            {contract.slice(0, 6)}…{contract.slice(-6)}
          </button>
          <a href={`https://dexscreener.com/search?q=${encodeURIComponent(contract)}`} target="_blank" rel="noreferrer"
             className="flex items-center gap-1.5 rounded-full border border-white/[0.08] px-2.5 py-1 font-mono text-[11px] text-bone-500 transition-colors hover:text-bone-200">
            <ArrowSquareOut size={12} /> DexScreener
          </a>
        </div>
      </div>

      {/* ── live trade tape: "X bought $Y just now" ── */}
      <LiveTape contract={contract} incoming={liveTrades} live={live} />

      {/* ── big signals chart: price × calls (tweets) × rides (wallet trades) ── */}
      <div className="panel p-4 sm:p-5">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-bone-200">
          <Megaphone size={15} weight="fill" className="text-flare" />
          <h2 className="font-display text-sm font-semibold uppercase tracking-wider">Price × KOL signals</h2>
          <LiveDot live={live} />
          <span className="ml-auto flex items-center gap-2">
            <SeriesToggle
              label="KOL Calls" color={CALLS_COLOR} count={chart?.calls.length ?? 0}
              on={showCalls} onClick={() => setShowCalls((v) => !v)}
            />
            <SeriesToggle
              label="KOL Rides" color={RIDES_COLOR} count={chart?.rides.length ?? 0}
              on={showRides} onClick={() => setShowRides((v) => !v)}
            />
          </span>
        </div>

        {/* timeframe chips (5m/15m/1h/4h/1D) — swap candle granularity */}
        <div className="mb-3 flex items-center gap-1 rounded-full border border-white/[0.06] p-0.5 w-fit">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className={`rounded-full px-2.5 py-1 font-mono text-[11px] transition-colors ${
                tf === t ? "bg-white/[0.08] text-bone-100" : "text-bone-600 hover:text-bone-300"
              }`}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        {chart === null ? (
          <div className="skeleton h-[320px] w-full rounded-xl" />
        ) : chart.candles.length === 0 ? (
          <div className="flex h-[320px] items-center justify-center rounded-xl border border-white/[0.05] bg-ink-900/40">
            <p className="font-mono text-xs text-bone-600">price history unavailable for this pool</p>
          </div>
        ) : (
          <CandleChart
            candles={chart.candles}
            calls={chart.calls}
            rides={chart.rides}
            showCalls={showCalls}
            showRides={showRides}
            height={320}
          />
        )}
        <p className="mt-2 flex flex-wrap items-center gap-x-1 text-[11px] text-bone-600">
          <Circle size={8} weight="fill" style={{ color: CALLS_COLOR }} />
          Calls — tracked smart accounts tweeting this token ·
          <Circle size={8} weight="fill" style={{ color: RIDES_COLOR }} />
          Rides — tracked KOL wallets trading it (tap a wallet for detail).
          Not financial advice.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ── Social Buzz: score-weighted X activity + KOL signal board ── */}
        <SocialBuzz contract={contract} bump={buzzBump} />

        {/* ── call feed + rides feed, stacked ── */}
        <div className="space-y-4">
          <div className="panel p-4 sm:p-5">
            <div className="mb-3 flex items-center gap-2 text-bone-200">
              <Megaphone size={15} weight="fill" style={{ color: CALLS_COLOR }} />
              <h2 className="font-display text-sm font-semibold uppercase tracking-wider">Latest calls</h2>
              <span className="ml-auto font-mono text-[11px] text-bone-600">{calls.length} tracked</span>
            </div>
            <div className="max-h-[240px] space-y-1.5 overflow-y-auto pr-1">
              {calls.length === 0 && (
                <p className="py-2 font-mono text-xs text-bone-600">No tracked calls yet.</p>
              )}
              {calls.map((c) => (
                <a key={c.tweetId} href={`https://x.com/i/status/${c.tweetId}`} target="_blank" rel="noreferrer"
                   className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-white/[0.04]">
                  {c.username && (
                    <Image src={avatar(c.username)} alt={c.username} width={22} height={22} unoptimized
                           className="h-[22px] w-[22px] rounded-full bg-ink-700 object-cover" />
                  )}
                  <span className="text-sm font-medium text-bone-200">@{c.username || "unknown"}</span>
                  <span className="font-mono text-[11px] text-bone-600">{timeAgo(c.ts)}</span>
                  {token.priceUsd != null && c.priceAtCall != null && c.priceAtCall > 0 && (
                    <span className={`ml-auto font-mono text-xs tabular-nums ${token.priceUsd >= c.priceAtCall ? "text-up" : "text-down"}`}>
                      {token.priceUsd >= c.priceAtCall ? "+" : ""}
                      {(((token.priceUsd - c.priceAtCall) / c.priceAtCall) * 100).toFixed(1)}%
                    </span>
                  )}
                </a>
              ))}
            </div>
          </div>

          <div className="panel p-4 sm:p-5">
            <div className="mb-3 flex items-center gap-2 text-bone-200">
              <Wallet size={15} weight="fill" style={{ color: RIDES_COLOR }} />
              <h2 className="font-display text-sm font-semibold uppercase tracking-wider">Latest rides</h2>
              <span className="ml-auto font-mono text-[11px] text-bone-600">
                {(chart?.rides.length ?? 0)} tracked wallet trade{(chart?.rides.length ?? 0) === 1 ? "" : "s"}
              </span>
            </div>
            <div className="max-h-[200px] space-y-1.5 overflow-y-auto pr-1">
              {(chart?.rides.length ?? 0) === 0 && (
                <p className="py-2 font-mono text-xs text-bone-600">
                  No tracked KOL wallet trades captured yet — rides accumulate as tracked
                  wallets trade this token.
                </p>
              )}
              {[...(chart?.rides ?? [])].reverse().map((r, i) => (
                <div key={i} className="flex items-center gap-2.5 rounded-lg px-2 py-1.5">
                  {r.username && (
                    <Image src={avatar(r.username)} alt={r.username} width={22} height={22} unoptimized
                           className="h-[22px] w-[22px] rounded-full bg-ink-700 object-cover" />
                  )}
                  <span className="text-sm font-medium text-bone-200">@{r.username || "tracked wallet"}</span>
                  {r.score != null && (
                    <span className="rounded bg-white/[0.05] px-1 py-px font-mono text-[9px] text-bone-500">
                      {Math.round(r.score)}
                    </span>
                  )}
                  <span className={`font-mono text-[11px] font-semibold uppercase ${r.side === "buy" ? "text-up" : "text-down"}`}>
                    {r.side}
                  </span>
                  <span className="font-mono text-[11px] text-bone-600">{timeAgo(r.ts)}</span>
                  {r.volumeUsd != null && (
                    <span className="ml-auto font-mono text-xs tabular-nums text-bone-400">
                      {fmtMoney(r.volumeUsd)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Connection state for the realtime stream — honest about it: a solid pulsing dot only
 *  when the socket is actually open, a muted "reconnecting" otherwise. */
function LiveDot({ live }: { live: boolean }) {
  return (
    <span
      className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide ${
        live ? "border-up/30 bg-up/10 text-up" : "border-white/[0.07] text-bone-600"
      }`}
      title={live ? "Streaming live" : "Reconnecting…"}
    >
      <span className={`relative flex h-1.5 w-1.5 ${live ? "" : "opacity-50"}`}>
        {live && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-up opacity-75" />}
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${live ? "bg-up" : "bg-bone-600"}`} />
      </span>
      {live ? "Live" : "Offline"}
    </span>
  );
}

function SeriesToggle({ label, color, count, on, onClick }: {
  label: string; color: string; count: number; on: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] transition-all ${
        on ? "border-white/20 bg-white/[0.06] text-bone-100" : "border-white/[0.07] text-bone-600 opacity-60"
      }`}
    >
      <span className="h-2 w-2 rounded-full" style={{ background: on ? color : "#555" }} />
      {label}
      <span className="tabular-nums text-bone-500">{count}</span>
    </button>
  );
}

function Stat({ k, v, sub, subUp, accent }: { k: string; v: string; sub?: string; subUp?: boolean; accent?: boolean }) {
  return (
    <div className="text-right">
      <div className="font-mono text-[10px] uppercase tracking-wide text-bone-600">{k}</div>
      <div className={`font-mono text-lg font-bold tabular-nums ${accent ? "text-flare" : "text-bone-100"}`}>{v}</div>
      {sub && <div className={`font-mono text-[10px] ${subUp ? "text-up" : "text-down"}`}>{sub}</div>}
    </div>
  );
}
