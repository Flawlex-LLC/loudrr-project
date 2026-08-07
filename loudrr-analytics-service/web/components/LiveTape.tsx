"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Pulse } from "@phosphor-icons/react";
import { getKolTape, type TapeTrade } from "@/lib/api";
import { avatar } from "@/lib/mock";

// Live trade tape — the "X bought $Y just now" ticker under the token chart. A KOL buyer
// (wallet in our vault) shows their handle, avatar and Loudrr score; an anonymous buyer shows
// an abbreviated address. Buys emphasised.
//
// One REST call paints the backlog instantly, then new trades arrive PUSHED from the page's
// single WebSocket (`incoming`) — no polling here. The old 6s poll is kept only as the
// fallback for when the socket can't connect, so a dead WS degrades to the previous
// behaviour instead of a frozen tape.

function fmtQty(v: number | null): string {
  if (v == null) return "";
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(2)}K`;
  return `$${v.toFixed(2)}`;
}

function secsAgo(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const POLL_MS = 6000;
const MAX_CHIPS = 24;

const keyOf = (t: TapeTrade) => `${t.ts}|${t.wallet}|${t.volumeUsd}`;

export function LiveTape({
  contract,
  incoming,
  live = false,
}: {
  contract: string;
  /** trades pushed from the page WebSocket (newest first) */
  incoming?: TapeTrade[];
  /** whether that socket is currently connected */
  live?: boolean;
}) {
  const [trades, setTrades] = useState<TapeTrade[] | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // backlog: one call so the strip paints immediately
  useEffect(() => {
    let dead = false;
    setTrades(null);
    getKolTape(contract, MAX_CHIPS).then((t) => {
      if (!dead) setTrades(t);
    });
    return () => {
      dead = true;
    };
  }, [contract]);

  // merge pushed trades; dedupe by identity because the backlog and the first live frame
  // legitimately overlap
  useEffect(() => {
    if (!incoming?.length) return;
    setTrades((prev) => {
      const seen = new Set((prev ?? []).map(keyOf));
      const fresh = incoming.filter((t) => !seen.has(keyOf(t)));
      if (!fresh.length) return prev;
      return [...fresh, ...(prev ?? [])].slice(0, MAX_CHIPS);
    });
  }, [incoming]);

  // fallback ONLY while the socket is down — otherwise the push path owns freshness
  useEffect(() => {
    if (live) {
      if (timer.current) { clearInterval(timer.current); timer.current = null; }
      return;
    }
    let dead = false;
    timer.current = setInterval(() => {
      getKolTape(contract, MAX_CHIPS).then((t) => {
        if (!dead && t.length) setTrades(t);
      });
    }, POLL_MS);
    return () => {
      dead = true;
      if (timer.current) { clearInterval(timer.current); timer.current = null; }
    };
  }, [contract, live]);

  // nothing to show (feed empty or unsupported pool) -> render nothing, never a broken strip
  if (trades !== null && trades.length === 0) return null;

  return (
    <div className="panel flex items-center gap-3 overflow-hidden px-3 py-2">
      <span className="flex shrink-0 items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-bone-500">
        <Pulse size={13} weight="bold" className={live ? "text-up" : "text-bone-600"} />
        Live
      </span>
      <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto">
        {trades === null
          ? Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="skeleton h-6 w-32 shrink-0 rounded-full" />
            ))
          : trades.map((t) => <TapeChip key={keyOf(t)} t={t} />)}
      </div>
    </div>
  );
}

function TapeChip({ t }: { t: TapeTrade }) {
  const buy = t.side === "buy";
  const body = (
    <span
      className={`flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 ${
        t.isKol ? "border-white/12 bg-white/[0.05]" : "border-white/[0.06]"
      }`}
    >
      {t.isKol && t.handle ? (
        <Image
          src={avatar(t.handle)}
          alt={t.handle}
          width={16}
          height={16}
          unoptimized
          className="h-4 w-4 rounded-full bg-ink-700 object-cover"
        />
      ) : null}
      <span className={`truncate text-[11px] font-medium ${t.isKol ? "text-bone-100" : "text-bone-500"}`}>
        {t.isKol ? `@${t.handle}` : t.wallet}
      </span>
      {t.score != null && (
        <span className="rounded bg-white/[0.06] px-1 font-mono text-[9px] text-bone-500">
          {Math.round(t.score)}
        </span>
      )}
      <span className={`font-mono text-[11px] font-semibold ${buy ? "text-up" : "text-down"}`}>
        {buy ? "bought" : "sold"}
      </span>
      <span className="font-mono text-[11px] tabular-nums text-bone-200">{fmtQty(t.volumeUsd)}</span>
      <span className="font-mono text-[10px] text-bone-600">{secsAgo(t.ts)}</span>
    </span>
  );
  return t.isKol && t.handle ? (
    <Link href={`/score/${t.handle}`} className="shrink-0 transition-opacity hover:opacity-80">
      {body}
    </Link>
  ) : (
    body
  );
}
