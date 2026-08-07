"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { X } from "@phosphor-icons/react";
import { avatar } from "@/lib/mock";
import type { LiveCandle } from "@/lib/live";

// Exchange-grade token chart: real candles + volume (TradingView's lightweight-charts, the
// same engine the exchanges ship), with Bitget's signature avatar pins layered on top.
//
// The pins are the reason this isn't just a stock chart: lightweight-charts' own markers are
// shapes/text only and can't render an image, so KOL avatars live in a DOM overlay whose
// positions we project through the chart's own coordinate system (timeToCoordinate /
// priceToCoordinate). They then pan and zoom in lockstep with the candles for free.

export type ChartCall = {
  username: string | null;
  tweetId: string;
  ts: string | null;
  priceAtCall: number | null;
};

export type ChartRide = {
  username: string | null;
  side: string;
  ts: string | null;
  price: number | null;
  volumeUsd?: number | null;
  score?: number | null;
  tier?: string | null;
  smartFollowers?: number | null;
};

export const CALLS_COLOR = "#f95400"; // orange — tweeted it
export const RIDES_COLOR = "#58c7f3"; // cyan — their tracked wallet traded it

const UP = "#22c07e";
const DOWN = "#f0507a";

const TIER_COLOR: Record<string, string> = {
  Megaphone: "#f95400", Amplifier: "#f5a623", Signal: "#58c7f3", Echo: "#8b93a7", Whisper: "#5b6270",
};

function fmtPrice(v: number): string {
  if (v >= 1000) return `$${v.toFixed(0)}`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toPrecision(3)}`;
}

function fmtQty(v: number): string {
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(2)}K`;
  return `$${v.toFixed(2)}`;
}

type Pin = {
  key: string;
  kind: "call" | "ride";
  username: string | null;
  time: UTCTimestamp;
  price: number;
  side?: string;
  ride?: ChartRide;
};

type Placed = Pin & { x: number; y: number };

export function CandleChart({
  candles,
  calls,
  rides = [],
  showCalls = true,
  showRides = true,
  height = 320,
}: {
  candles: LiveCandle[];
  calls: ChartCall[];
  rides?: ChartRide[];
  showCalls?: boolean;
  showRides?: boolean;
  height?: number;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const [placed, setPlaced] = useState<Placed[]>([]);
  const [openRide, setOpenRide] = useState<Placed | null>(null);
  const [hover, setHover] = useState<Placed | null>(null);

  // ── pins (independent of the chart instance) ──────────────────────────────
  const pins = useMemo<Pin[]>(() => {
    const out: Pin[] = [];
    const at = (iso: string | null): UTCTimestamp | null =>
      iso ? (Math.floor(new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime() / 1000) as UTCTimestamp) : null;
    // fall back to the nearest candle's close when a call has no captured price
    const near = (t: number): number => {
      let best = candles[0];
      for (const c of candles) if (Math.abs(c.ts - t) < Math.abs(best.ts - t)) best = c;
      return best?.close ?? 0;
    };
    if (showCalls) {
      for (const c of calls) {
        const t = at(c.ts);
        if (t == null) continue;
        out.push({
          key: `c-${c.tweetId}-${t}`, kind: "call", username: c.username, time: t,
          price: c.priceAtCall && c.priceAtCall > 0 ? c.priceAtCall : near(t),
        });
      }
    }
    if (showRides) {
      for (const [i, r] of rides.entries()) {
        const t = at(r.ts);
        if (t == null) continue;
        out.push({
          key: `r-${i}-${t}`, kind: "ride", username: r.username, time: t,
          price: r.price && r.price > 0 ? r.price : near(t),
          side: r.side, ride: r,
        });
      }
    }
    return out;
  }, [calls, rides, candles, showCalls, showRides]);
  const pinsRef = useRef(pins);
  pinsRef.current = pins;

  // ── project pins into pixel space through the chart's own scales ──────────
  const reposition = useCallback(() => {
    const chart = chartRef.current;
    const series = priceRef.current;
    if (!chart || !series) return;
    const ts = chart.timeScale();
    const out: Placed[] = [];
    for (const p of pinsRef.current) {
      const x = ts.timeToCoordinate(p.time as Time);
      const y = series.priceToCoordinate(p.price);
      if (x == null || y == null) continue;   // scrolled out of view
      out.push({ ...p, x, y });
    }
    setPlaced(out);
  }, []);

  // rAF-coalesced: the range-change callback fires on every frame of a drag, and
  // re-projecting every pin synchronously inside it makes panning stutter.
  const raf = useRef<number | null>(null);
  const queueReposition = useCallback(() => {
    if (raf.current != null) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = null;
      reposition();
    });
  }, [reposition]);

  // ── chart lifecycle: created ONCE, never per data change ──────────────────
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;

    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b93a7",
        fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.07)", scaleMargins: { top: 0.12, bottom: 0.26 } },
      timeScale: { borderColor: "rgba(255,255,255,0.07)", timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(255,255,255,0.2)", labelBackgroundColor: "#1a1a1a" },
        horzLine: { color: "rgba(255,255,255,0.2)", labelBackgroundColor: "#1a1a1a" },
      },
      handleScale: { axisPressedMouseMove: { time: true, price: false } },
    });

    const price = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderVisible: false,
      wickUpColor: UP, wickDownColor: DOWN,
      priceFormat: { type: "price", precision: 6, minMove: 0.000001 },
    });
    // volume shares the pane, pinned to the bottom quarter on its own hidden scale
    const vol = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    chartRef.current = chart;
    priceRef.current = price;
    volRef.current = vol;

    chart.timeScale().subscribeVisibleLogicalRangeChange(queueReposition);
    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth });
      queueReposition();
    });
    ro.observe(el);
    chart.applyOptions({ width: el.clientWidth });

    return () => {
      ro.disconnect();
      if (raf.current != null) cancelAnimationFrame(raf.current);
      chart.remove();
      chartRef.current = null;
      priceRef.current = null;
      volRef.current = null;
    };
  }, [height, queueReposition]);

  // series identity: the first bar's timestamp is stable across live appends but changes when
  // the token or timeframe switches (a fresh series loads). We fit the view ONLY on that
  // change — fitting on every live tick would yank the user's zoom/pan back on each new candle.
  const seriesKey = candles.length ? candles[0].ts : 0;
  const fittedFor = useRef<number | null>(null);

  // ── data: setData on change, then re-project the pins ─────────────────────
  useEffect(() => {
    const price = priceRef.current;
    const vol = volRef.current;
    if (!price || !vol || candles.length === 0) return;
    price.setData(
      candles.map((c) => ({
        time: c.ts as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close,
      })),
    );
    vol.setData(
      candles.map((c) => ({
        time: c.ts as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? "rgba(34,192,126,0.35)" : "rgba(240,80,122,0.35)",
      })),
    );
    if (fittedFor.current !== seriesKey) {
      chartRef.current?.timeScale().fitContent();
      fittedFor.current = seriesKey;
    }
    queueReposition();
  }, [candles, seriesKey, queueReposition]);

  useEffect(() => {
    queueReposition();
  }, [pins, queueReposition]);

  const showTip = hover && !openRide;

  return (
    <div className="relative w-full">
      <div ref={boxRef} className="w-full" style={{ height }} />

      {/* avatar pin overlay — pointer-events only on the pins themselves so the chart
          underneath keeps its native pan/zoom/crosshair */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {placed.map((p) => {
          const color = p.kind === "call" ? CALLS_COLOR : RIDES_COLOR;
          return (
            <button
              key={p.key}
              className="pointer-events-auto absolute grid h-[21px] w-[21px] -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-[1.5px] bg-ink-900 transition-transform hover:scale-125"
              style={{ left: p.x, top: p.y, borderColor: color, zIndex: p.kind === "ride" ? 2 : 1 }}
              onMouseEnter={() => setHover(p)}
              onMouseLeave={() => setHover(null)}
              onClick={() => p.kind === "ride" && (setHover(null), setOpenRide(p))}
              aria-label={`@${p.username ?? "unknown"} ${p.kind === "call" ? "called" : p.side}`}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={avatar(p.username || "x")}
                alt=""
                className="h-[17px] w-[17px] rounded-full bg-ink-700 object-cover"
              />
              {p.kind === "ride" && (
                <span
                  className="absolute h-0 w-0 border-x-[3.5px] border-x-transparent"
                  style={
                    p.side === "buy"
                      ? { top: -8, borderBottom: `5px solid ${RIDES_COLOR}` }
                      : { bottom: -8, borderTop: `5px solid ${RIDES_COLOR}` }
                  }
                />
              )}
            </button>
          );
        })}
      </div>

      {showTip && (
        <div
          className="pointer-events-none absolute z-20 -translate-x-1/2 rounded-lg border border-white/10 bg-ink-900/95 px-2.5 py-1.5 shadow-xl backdrop-blur"
          style={{ left: Math.min(Math.max(hover.x, 70), (boxRef.current?.clientWidth ?? 600) - 70), top: Math.max(0, hover.y - 54) }}
        >
          <div className="whitespace-nowrap text-xs font-medium text-bone-100">
            @{hover.username || "unknown"}
          </div>
          <div className="whitespace-nowrap font-mono text-[10px] text-bone-400">
            {hover.kind === "call"
              ? `called at ${fmtPrice(hover.price)}`
              : `${hover.side === "buy" ? "bought" : "sold"}${
                  hover.ride?.volumeUsd ? " " + fmtQty(hover.ride.volumeUsd) : ""
                } · tap for detail`}
          </div>
        </div>
      )}

      {openRide?.ride && (
        <>
          <div className="absolute inset-0 z-20" onClick={() => setOpenRide(null)} />
          <div
            className="absolute z-30 w-[212px] -translate-x-1/2 rounded-xl border border-white/12 bg-ink-900/97 p-3 shadow-2xl backdrop-blur"
            style={{
              left: Math.min(Math.max(openRide.x, 108), (boxRef.current?.clientWidth ?? 600) - 108),
              top: Math.max(4, openRide.y - 128),
            }}
          >
            <div className="flex items-center gap-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={avatar(openRide.username || "x")} alt="" className="h-8 w-8 rounded-full bg-ink-700 object-cover" />
              <div className="min-w-0">
                <a
                  href={openRide.username ? `/score/${openRide.username}` : undefined}
                  className="block truncate text-sm font-semibold text-bone-100 hover:text-flare"
                >
                  @{openRide.username || "tracked wallet"}
                </a>
                {openRide.ride.tier && (
                  <span
                    className="rounded px-1 py-px font-mono text-[9px] font-semibold uppercase"
                    style={{
                      color: TIER_COLOR[openRide.ride.tier] ?? "#8b93a7",
                      background: `${TIER_COLOR[openRide.ride.tier] ?? "#8b93a7"}1a`,
                    }}
                  >
                    {openRide.ride.tier}
                    {openRide.ride.score != null ? ` · ${Math.round(openRide.ride.score)}` : ""}
                  </span>
                )}
              </div>
              <button onClick={() => setOpenRide(null)} className="ml-auto text-bone-600 hover:text-bone-200" aria-label="Close">
                <X size={13} weight="bold" />
              </button>
            </div>
            <div className="mt-2.5 space-y-1 font-mono text-[11px]">
              <Row k="Action" v={
                <span className={openRide.side === "buy" ? "text-up" : "text-down"}>
                  {openRide.side === "buy" ? "Bought" : "Sold"}
                </span>} />
              {openRide.ride.volumeUsd != null && (
                <Row k="Amount" v={<span className="text-bone-100">{fmtQty(openRide.ride.volumeUsd)}</span>} />
              )}
              <Row k="Price" v={<span className="text-bone-300">{fmtPrice(openRide.price)}</span>} />
              <Row k="When" v={
                <span className="text-bone-400">
                  {openRide.ride.ts
                    ? new Date(openRide.ride.ts + "Z").toLocaleString("en-US", {
                        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                      })
                    : "—"}
                </span>} />
              {openRide.ride.smartFollowers != null && (
                <Row k="Smart followers" v={<span className="text-bone-400">{openRide.ride.smartFollowers.toLocaleString()}</span>} />
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-bone-600">{k}</span>
      {v}
    </div>
  );
}
