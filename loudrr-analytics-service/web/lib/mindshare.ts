import { LEADERBOARD, avatar } from "./mock";

export type Vertical = "crypto" | "ai" | "stocks";

export const VERTICALS: { key: Vertical; label: string; live: boolean }[] = [
  { key: "stocks", label: "Stocks", live: false },
  { key: "ai", label: "AI", live: false },
  { key: "crypto", label: "Crypto", live: true },
];

export type MSItem = {
  id: string;
  name: string;
  sub: string; // @handle or TICKER
  color: string; // brand/monogram color (companies)
  img?: string; // avatar (voices)
  share: number; // mindshare %
  d7: number; // Δ7d in bps
  d30: number; // Δ30d in bps
  cats: string[]; // category tags (besides "All")
};

export const VOICE_CATS = ["All", "Perp DEX", "Info Market"];
export const COMPANY_CATS = ["All", "Pre-TGE", "Info Markets", "Exchange", "Project Leaderboard"];

// ── crypto voices — derived from the leaderboard ────────────────────────────
export const CRYPTO_VOICES: MSItem[] = [...LEADERBOARD]
  .sort((a, b) => b.mindsharePct - a.mindsharePct)
  .slice(0, 30)
  .map((p, i) => ({
    id: p.handle,
    name: p.name,
    sub: "@" + p.handle,
    color: "#f95400",
    img: avatar(p.handle),
    share: Math.max(0.05, p.mindsharePct),
    d7: p.mindshareDeltaBps,
    d30: Math.round(p.mindshareDeltaBps * 1.8 + (((i * 37) % 23) - 11)),
    cats: i % 4 === 0 ? ["Perp DEX"] : i % 4 === 2 ? ["Info Market"] : [],
  }));

// ── crypto companies / tokens ───────────────────────────────────────────────
function c(name: string, sub: string, color: string, share: number, d7: number, d30: number, cats: string[]): MSItem {
  return { id: sub, name, sub, color, share, d7, d30, cats };
}

export const CRYPTO_COMPANIES: MSItem[] = [
  c("Bitcoin", "BTC", "#f7931a", 15.22, 34, 193, ["Project Leaderboard"]),
  c("Ethereum", "ETH", "#627eea", 7.55, -12, 41, ["Project Leaderboard"]),
  c("USDC", "USDC", "#2775ca", 4.13, 144, 127, ["Project Leaderboard"]),
  c("Solana", "SOL", "#14f195", 4.1, 22, 60, ["Project Leaderboard"]),
  c("Hyperliquid", "HYPE", "#0aa37f", 3.42, 18, 70, ["Exchange"]),
  c("BNB", "BNB", "#f0b90b", 2.69, -6, 12, ["Exchange"]),
  c("Tether", "USDT", "#26a17b", 1.72, 9, 22, ["Project Leaderboard"]),
  c("Pump.fun", "PUMP", "#22c55e", 1.68, 27, -14, ["Pre-TGE"]),
  c("Aave", "AAVE", "#b6509e", 1.09, 11, 37, ["Project Leaderboard"]),
  c("OKX", "OKB", "#0052ff", 1.08, -4, 9, ["Exchange"]),
  c("Uniswap", "UNI", "#ff007a", 0.97, 6, -8, ["Exchange"]),
  c("Kraken", "KRKN", "#5741d9", 0.94, 3, 18, ["Exchange"]),
  c("Ethena", "ENA", "#7c6cff", 0.91, 58, 48, ["Project Leaderboard"]),
  c("Polymarket", "POLY", "#1652f0", 0.71, 38, 52, ["Info Markets"]),
  c("Lido", "LDO", "#00a3ff", 0.72, 46, 57, ["Project Leaderboard"]),
  c("Jupiter", "JUP", "#10b981", 0.66, -9, 14, ["Project Leaderboard"]),
  c("Coinbase", "COIN", "#0052ff", 0.61, 12, 33, ["Exchange"]),
  c("Monad", "MON", "#836ef9", 0.62, 71, 110, ["Pre-TGE"]),
  c("Bybit", "BYBIT", "#f7a600", 0.57, 94, 96, ["Exchange"]),
  c("Arbitrum", "ARB", "#28a0f0", 0.58, 8, -12, ["Project Leaderboard"]),
  c("Pyth", "PYTH", "#9b87f5", 0.54, 23, -37, ["Project Leaderboard"]),
  c("Optimism", "OP", "#ff0420", 0.49, -5, 7, ["Project Leaderboard"]),
  c("MegaETH", "MEGA", "#00e0c6", 0.41, 19, 64, ["Pre-TGE"]),
  c("dYdX", "DYDX", "#6966ff", 0.39, 4, -6, ["Exchange"]),
  c("Kalshi", "KAL", "#00d09c", 0.33, 29, 23, ["Info Markets"]),
  c("Maker", "MKR", "#1aab9b", 0.44, 6, 16, ["Project Leaderboard"]),
  c("Eclipse", "ES", "#a78bfa", 0.22, 13, 41, ["Pre-TGE"]),
  c("Myriad", "MYR", "#f43f5e", 0.18, 17, 9, ["Info Markets"]),
];

export function filterByCat(items: MSItem[], cat: string): MSItem[] {
  const f = cat === "All" ? items : items.filter((i) => i.cats.includes(cat));
  return [...f].sort((a, b) => b.share - a.share);
}

// ── squarified treemap (Bruls/Huizing/van Wijk) → rects in a W×H coordinate ──
export type Rect = { x: number; y: number; w: number; h: number };

export function squarify(values: number[], W: number, H: number): Rect[] {
  const n = values.length;
  const rects: Rect[] = new Array(n);
  if (!n) return rects;
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const items = values.map((v, i) => ({ i, area: (v / total) * (W * H) }));

  let x = 0;
  let y = 0;
  let w = W;
  let h = H;

  const worst = (row: { area: number }[], side: number) => {
    if (!row.length) return Infinity;
    const sum = row.reduce((a, r) => a + r.area, 0);
    const mx = Math.max(...row.map((r) => r.area));
    const mn = Math.min(...row.map((r) => r.area));
    const s2 = sum * sum;
    const side2 = side * side;
    return Math.max((side2 * mx) / s2, s2 / (side2 * mn));
  };

  const layout = (row: { i: number; area: number }[]) => {
    const wide = w >= h;
    const side = Math.min(w, h);
    const sum = row.reduce((a, r) => a + r.area, 0);
    const thickness = side ? sum / side : 0;
    let pos = wide ? y : x;
    for (const r of row) {
      const len = thickness ? r.area / thickness : 0;
      if (wide) {
        rects[r.i] = { x, y: pos, w: thickness, h: len };
        pos += len;
      } else {
        rects[r.i] = { x: pos, y, w: len, h: thickness };
        pos += len;
      }
    }
    if (wide) {
      x += thickness;
      w -= thickness;
    } else {
      y += thickness;
      h -= thickness;
    }
  };

  let row: { i: number; area: number }[] = [];
  let idx = 0;
  while (idx < n) {
    const side = Math.min(w, h);
    const next = items[idx];
    if (row.length === 0 || worst(row, side) >= worst([...row, next], side)) {
      row.push(next);
      idx++;
    } else {
      layout(row);
      row = [];
    }
  }
  if (row.length) layout(row);
  return rects;
}
