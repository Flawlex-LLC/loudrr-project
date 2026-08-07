"use client";

import { useEffect, useRef, useState } from "react";

// ── Loudrr realtime client ───────────────────────────────────────────────────
// One WebSocket per open token page, fed by the server-side hub (app/engagement/live.py).
// The server polls the free feed ONCE per token and fans out, so this costs nothing extra
// per viewer. Frames: hello | trades | candle | call.
//
// Reconnect is exponential with jitter and capped: a backend restart or a laptop waking from
// sleep must not turn N tabs into a synchronised reconnect stampede against our own API.

export type LiveCandle = {
  ts: number; open: number; high: number; low: number; close: number; volume: number;
  price: number;
};

export type LiveTrade = {
  wallet: string; handle: string | null; isKol: boolean;
  score: number | null; tier: string | null;
  side: string; volumeUsd: number | null; priceUsd: number | null; ts: string | null;
};

export type LiveCall = {
  username: string | null; tweetId: string; ts: string | null; priceAtCall: number | null;
  confidence?: string;
};

/** A tracked KOL wallet trade, pushed the moment the swap lands onchain. Shaped to match
 *  KolChart["rides"] so a live ride and a REST-loaded ride render identically. */
export type LiveRide = {
  username: string | null; side: string; ts: string | null;
  price: number | null; volumeUsd: number | null;
  score: number | null; tier: string | null; smartFollowers: number | null;
  mint: string; tokenAmount: number;
};

type Frame =
  | { type: "hello"; contract: string; timeframe: string; pollSeconds: number }
  | { type: "trades"; contract: string; trades: LiveTrade[] }
  | { type: "candle"; contract: string; timeframe: string; candle: LiveCandle }
  | { type: "call"; contract: string; call: LiveCall }
  | { type: "ride"; contract: string; ride: LiveRide };

const API = process.env.NEXT_PUBLIC_LOUDRR_API ?? "";

/** http(s)://host/v1 -> ws(s)://host/v1 . Relative/blank API => no socket. */
export function liveUrl(contract: string, timeframe: string): string | null {
  if (!API) return null;
  try {
    const base = new URL(API, typeof window === "undefined" ? undefined : window.location.href);
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
    base.pathname = `${base.pathname.replace(/\/$/, "")}/kol-calls/live`;
    base.search = `?contract=${encodeURIComponent(contract)}&timeframe=${encodeURIComponent(timeframe)}`;
    return base.toString();
  } catch {
    return null;
  }
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 20000;

export type LiveHandlers = {
  onTrades?: (t: LiveTrade[]) => void;
  onCandle?: (c: LiveCandle) => void;
  onCall?: (c: LiveCall) => void;
  onRide?: (r: LiveRide) => void;
};

/**
 * Subscribe to a token's realtime stream. Returns {live} for a connection dot.
 *
 * Handlers are kept in a ref, so a parent re-render never tears down the socket — the effect
 * depends only on (contract, timeframe), the two things that genuinely change the stream.
 */
export function useLiveToken(
  contract: string | null,
  timeframe: string,
  handlers: LiveHandlers,
): { live: boolean } {
  const [live, setLive] = useState(false);
  const hRef = useRef(handlers);
  hRef.current = handlers;

  useEffect(() => {
    if (!contract) return;
    const url = liveUrl(contract, timeframe);
    if (!url) return;

    let ws: WebSocket | null = null;
    let dead = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let keepalive: ReturnType<typeof setInterval> | null = null;

    const connect = () => {
      if (dead) return;
      try {
        ws = new WebSocket(url);
      } catch {
        schedule();
        return;
      }

      ws.onopen = () => {
        if (dead) return;
        attempt = 0;
        setLive(true);
        // The server reads to detect a dead socket; this also keeps intermediaries
        // (Coolify/Traefik) from idling us out on a quiet pool.
        keepalive = setInterval(() => {
          if (ws?.readyState === WebSocket.OPEN) ws.send("ping");
        }, 25000);
      };

      ws.onmessage = (ev) => {
        if (dead) return;
        let f: Frame;
        try {
          f = JSON.parse(ev.data as string) as Frame;
        } catch {
          return;             // a malformed frame is skipped, never fatal
        }
        if (f.type === "trades") hRef.current.onTrades?.(f.trades);
        else if (f.type === "candle") {
          // Guard against a frame for another granularity (the hub serves every timeframe
          // its viewers are watching on one topic) — merging a 5m bar into a 4h series
          // would silently corrupt the chart.
          if (f.timeframe === timeframe) hRef.current.onCandle?.(f.candle);
        } else if (f.type === "call") hRef.current.onCall?.(f.call);
        else if (f.type === "ride") hRef.current.onRide?.(f.ride);
      };

      ws.onclose = () => {
        if (keepalive) { clearInterval(keepalive); keepalive = null; }
        setLive(false);
        schedule();
      };
      ws.onerror = () => ws?.close();
    };

    const schedule = () => {
      if (dead) return;
      // exponential + jitter: many tabs reconnecting must not land on the same millisecond
      const wait = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
      attempt += 1;
      timer = setTimeout(connect, wait * (0.6 + Math.random() * 0.6));
    };

    connect();
    return () => {
      dead = true;
      setLive(false);
      if (timer) clearTimeout(timer);
      if (keepalive) clearInterval(keepalive);
      if (ws) {
        ws.onclose = null;    // unhook first: our own teardown must not trigger a reconnect
        ws.close();
      }
    };
  }, [contract, timeframe]);

  return { live };
}
