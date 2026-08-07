"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { CaretRight, Megaphone } from "@phosphor-icons/react";
import { getKolCalls, USE_LIVE, type KolCallsItem as Item } from "@/lib/api";
import { avatar } from "@/lib/mock";

// Bitget-style "KOL Signals": token leaderboard ranked by tracked-KOL calls in a window,
// joined with live market data (served from our DB cache — zero external calls per view).
// Rows navigate to the dedicated analytics page at /kol-signals/[contract].

const WINDOWS = ["24h", "7d", "30d"] as const;
type Win = (typeof WINDOWS)[number];

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toPrecision(3)}`;
}

export function KolSignals() {
  const [win, setWin] = useState<Win>("24h");
  const [items, setItems] = useState<Item[] | null>(null);
  const [failed, setFailed] = useState(false);
  // per-window session cache: flipping back to a window paints instantly instead of
  // re-running the leaderboard query and flashing skeletons
  const cache = useRef<Partial<Record<Win, Item[]>>>({});

  useEffect(() => {
    let dead = false;
    setFailed(false);
    if (!USE_LIVE) {
      setFailed(true);
      return;
    }
    const cached = cache.current[win];
    setItems(cached ?? null);
    getKolCalls(win)
      .then((list) => {
        if (dead) return;
        if (list === null) {
          if (!cached) setFailed(true);
          return;
        }
        cache.current[win] = list;
        setItems(list);
      })
      .catch(() => !dead && !cached && setFailed(true));
    return () => {
      dead = true;
    };
  }, [win]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">Off-chain social sentiment · on-chain reality</p>
          <h1 className="mt-1 font-display text-2xl font-bold tracking-tightest">KOL Signals</h1>
          <p className="mt-1 max-w-xl text-sm text-bone-400">
            The hottest tokens called by tracked smart accounts. <span className="text-bone-300">Calls</span> count
            their tweets naming a ticker or contract — <span className="text-bone-300">Rides</span> (tracked KOL
            wallets buying) coming soon.
          </p>
        </div>
        <div className="flex gap-1.5">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setWin(w)}
              className={`rounded-full border px-3 py-1.5 font-mono text-xs uppercase transition-colors ${
                win === w
                  ? "border-white/20 bg-white/[0.06] text-bone-100"
                  : "border-white/[0.07] text-bone-500 hover:text-bone-200"
              }`}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      {failed || (items && items.length === 0) ? (
        <div className="panel flex flex-col items-center gap-2 px-6 py-14 text-center">
          <Megaphone size={26} className="text-bone-600" />
          <p className="font-medium text-bone-300">No calls in this window yet</p>
          <p className="max-w-sm text-sm text-bone-500">
            Tracking is warming up — calls appear as tracked smart accounts post tickers and
            contracts.
          </p>
        </div>
      ) : items === null ? (
        <div className="panel divide-y divide-white/[0.06] overflow-hidden">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-4">
              <div className="skeleton h-6 w-6 rounded-full" />
              <div className="skeleton h-9 w-9 rounded-full" />
              <div className="flex-1 space-y-2">
                <div className="skeleton h-3.5 w-28 rounded" />
                <div className="skeleton h-3 w-40 rounded" />
              </div>
              <div className="skeleton h-8 w-14 rounded" />
            </div>
          ))}
        </div>
      ) : (
        <div className="panel divide-y divide-white/[0.06] overflow-hidden">
          {items.map((it, i) => (
            <TokenRow key={it.contract} item={it} rank={i + 1} />
          ))}
        </div>
      )}

      <p className="text-[11px] text-bone-600">
        Calls are token mentions by tracked smart accounts — not financial advice. Market data
        via public DEX sources; blank days before tracking began are not zero-activity.
      </p>
    </div>
  );
}

function TokenRow({ item, rank }: { item: Item; rank: number }) {
  const chg = item.change24h;

  return (
    <div>
      <Link
        href={`/kol-signals/${encodeURIComponent(item.contract)}`}
        className="grid w-full grid-cols-[28px_1fr_auto] items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-white/[0.03] sm:grid-cols-[28px_1.4fr_1fr_110px_20px]"
      >
        <span className={`font-mono text-sm tabular-nums ${rank <= 3 ? "text-flare" : "text-bone-500"}`}>{rank}</span>

        <span className="flex min-w-0 items-center gap-3">
          {item.image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={item.image} alt="" className="h-9 w-9 shrink-0 rounded-full bg-ink-700 object-cover" />
          ) : (
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink-700 font-mono text-xs text-bone-500">
              {(item.symbol || "?").slice(0, 3)}
            </span>
          )}
          <span className="min-w-0">
            <span className="flex items-center gap-2">
              <span className="truncate font-semibold text-bone-100">{item.symbol || "???"}</span>
              {item.chain && (
                <span className="rounded-full border border-white/10 px-1.5 py-0.5 font-mono text-[9px] uppercase text-bone-500">
                  {item.chain}
                </span>
              )}
            </span>
            <span className="mt-0.5 flex items-center gap-1.5 text-xs">
              <span className="text-bone-500">MC {fmtMoney(item.mcapUsd)}</span>
              {chg != null && (
                <span className={chg >= 0 ? "text-up" : "text-down"}>
                  {chg >= 0 ? "+" : ""}
                  {chg.toFixed(2)}%
                </span>
              )}
            </span>
          </span>
        </span>

        {/* KOL avatar strip */}
        <span className="hidden items-center sm:flex">
          <span className="flex -space-x-2">
            {item.kolSample.slice(0, 5).map((k) => (
              <Image
                key={k.username}
                src={avatar(k.username)}
                alt={k.username}
                width={22}
                height={22}
                unoptimized
                className="h-[22px] w-[22px] rounded-full border border-ink-900 bg-ink-700 object-cover"
              />
            ))}
          </span>
          <span className="ml-2 font-mono text-xs text-bone-500">
            {item.kols} KOL{item.kols === 1 ? "" : "s"}
          </span>
        </span>

        <span className="text-right">
          <span className="block font-mono text-lg font-bold tabular-nums text-flare">{item.calls}</span>
          <span className="block font-mono text-[10px] uppercase tracking-wide text-bone-500">calls</span>
        </span>

        <CaretRight size={14} className="hidden text-bone-600 sm:block" />
      </Link>
    </div>
  );
}
