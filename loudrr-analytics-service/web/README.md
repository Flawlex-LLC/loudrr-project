# Loudrr — web

The marketing + product front-end for **Loudrr**, the influence score for crypto X.
A dense, Kaito-grade dashboard — score history, mindshare, smart-follower breakdowns,
a live trust-graph viz — not just a score and a table.

> Lives inside the analytics-service repo for now but is **self-contained** (own
> `package.json`) and will move to its own container/repo later. The backend repo stays
> API-only.

## Stack (all latest)

- **Next.js 16** (app router, Turbopack) · **React 19**
- **Tailwind CSS v4** — CSS-first `@theme` tokens in `app/globals.css` (no `tailwind.config`)
- **Geist + Geist Mono** (via the `geist` package — no Google fetch)
- **Phosphor Icons** (fill weight, set globally in `components/Providers.tsx`)
- **framer-motion** for modal/reveal motion; charts are hand-rolled SVG (no chart lib)

```bash
cd web
npm install
npm run dev      # http://localhost:3000
npm run build    # production build / typecheck
```

## Routes

| Route             | What's there                                                                          |
| ----------------- | ------------------------------------------------------------------------------------- |
| `/`               | Landing — hero search, live KPI strip, leaderboard teaser w/ sparklines, method, tiers |
| `/score/[handle]` | The dashboard — see below                                                              |
| `/leaderboard`    | Sortable table: per-row sparklines, SF + ratio cols, rank movement, window + density   |
| `/developers`     | API docs — free keyless endpoint, code samples, gated key panel, sales tiers           |

## The score dashboard (`components/ScoreDashboard.tsx`)

Closes the Kaito/TwitterScore gap list from the teardown:

- **Hero** — glowing signal-ring gauge (count-up), tier, score-distribution bar, badges
- **Time-window control** (24H/7D/30D/3M/1Y) driving the chart, deltas and KPIs
- **KPI row** — score, smart followers, SF ratio (vs 10% benchmark), mindshare, mentions — each with a dual abs+% delta and a background sparkline
- **Score-history chart** — area chart with crosshair + follow tooltip
- **Mindshare donut** + momentum/sentiment
- **Smart-followers grid** — role-tabbed (Influencers/Projects/Funds/Founders/Angels/Media/Exchanges)
- **Audience composition** stacked bar + smart-follower-ratio meter
- **Engagement panel** — rate / likes / reposts / replies / follower-ratio / bot-% with named tiers
- **Trust graph** — radial network constellation with signal pulses travelling the edges
- **Follow history** — timeline of who started/stopped following, filterable

### Components
`components/charts/` — `Sparkline`, `ScoreHistoryChart`, `MindshareDonut`, `ScorePositionBar`,
`AudienceBreakdown`, `RatioMeter`, `NetworkConstellation` (all pure SVG).
`components/` — `KpiCard`, `DeltaPair`, `RankMovement`, `TimeWindowChips`, `SmartFollowersGrid`,
`EngagementPanel`, `BadgeRail`, `FriendshipHistory`, `ProfileHeaderRich`, `SignalMeter`, …

## Gating model (important)

Nothing is behind a hard wall — **everything is browsable**. Login is **action-triggered** via
a context modal (`components/AuthGate.tsx`):

- Looking up a **4th profile** → sign-in modal (`lib/gate.ts`, free limit = 3); the 4th dashboard
  renders blurred with a "sign in to reveal" overlay.
- **"Get API key"** on `/developers` → sign-in modal; the key only renders once signed in.

Open it anywhere with `useAuthGate().requireLogin(reason?)`. Auth is mocked (localStorage
`loudrr.auth.v1`); swap `completeLogin()` for real OAuth later.

## Data

`lib/mock.ts` is seeded with the **real calibrated Loudrr Scores** (Elon 5853, Vitalik 4864, …)
and augments each profile with **deterministic, hash-seeded depth** (90/365-day history, role
breakdown, engagement, mindshare, friendship events…) so the dashboard is dense and stable across
SSR/CSR — no `Date`/`Math.random` at render, so no hydration drift. Unknown handles get a
deterministic synthetic profile.

Going live is a one-line change in `lib/api.ts`:

```
NEXT_PUBLIC_LOUDRR_API=https://api.loudrr.com   # then set USE_LIVE = true
GET {API}/score?userName=elonmusk  ->  { "userName": "elonmusk", "score": 5853 }
```

The public score endpoint is **keyless + free** (the marketing funnel). Enriched fields (rank,
percentile, breakdowns) come from the authed/keyed tier.

## Design tokens (`app/globals.css` `@theme`)

- **Canvas** — deep cool near-black `ink` (#07070b→#262633) with an ambient orb field
- **Accent** — Loudrr orange `flare` (#ff6a00); `up`/`down` for deltas; `iris` for second series
- **Surfaces** — `.glass` (gradient-hairline frosted) and `.panel`; `.grain` scanline overlay
- **Motif** — the logo's signal rings: the gauge, pulsing rings, edge pulses on the graph
