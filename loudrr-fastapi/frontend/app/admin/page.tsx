'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Layers,
  ShieldCheck,
  UserCheck,
} from 'lucide-react';
import {
  adminApi,
  type AdminStats,
  type AdminUserRow,
  type TimeseriesResponse,
} from '@/lib/api';
import { EmptyState } from '@/components/admin/EmptyState';
import { Badge } from '@/components/admin/Badge';
import { Skeleton, TableSkeleton } from '@/components/admin/Skeleton';
import { HeroMetric } from '@/components/admin/HeroMetric';
import { AreaChart } from '@/components/admin/AreaChart';
import { MetricRow } from '@/components/admin/MetricRow';
import { DonutChart } from '@/components/admin/DonutChart';
import { cn } from '@/lib/utils';

// ---------- helpers ----------

function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return n.toLocaleString();
}

function fmtKarma(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 5) return 'just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMo = Math.round(diffDay / 30);
  if (diffMo < 12) return `${diffMo}mo ago`;
  const diffYr = Math.round(diffMo / 12);
  return `${diffYr}y ago`;
}

function shortId(id: string | null | undefined): string {
  if (!id) return '—';
  if (id.length >= 8) return id.slice(0, 8);
  return id;
}

type AuditTone = 'success' | 'danger' | 'warning' | 'info' | 'neutral';

function actionTone(action: string): AuditTone {
  const a = action.toLowerCase();
  if (a.includes('approve') || a.includes('grant') || a.includes('unban') || a.includes('whitelist_add')) {
    return 'success';
  }
  if (a.includes('reject') || a.includes('revoke') || a.includes('ban') || a.includes('delete')) {
    return 'danger';
  }
  if (a.includes('update') || a.includes('edit') || a.includes('change')) {
    return 'warning';
  }
  if (a.includes('create') || a.includes('view') || a.includes('search')) {
    return 'info';
  }
  return 'neutral';
}

function detailSummary(detail: Record<string, unknown>): string {
  if (!detail || Object.keys(detail).length === 0) return '—';
  try {
    const json = JSON.stringify(detail);
    if (json.length <= 60) return json;
    return json.slice(0, 57) + '…';
  } catch {
    return '—';
  }
}

// Same bands as backend/app/services/tier.py (hardcoded defaults — admin may have
// retuned the thresholds via TIER_*_THRESHOLD SiteSettings, but the frontend has
// no live access to those, so we use the source-of-truth defaults).
const TIER_BANDS: Array<{ name: string; min: number; color: string }> = [
  { name: 'GOAT', min: 1000, color: '#f95400' }, // brightest orange
  { name: 'OG', min: 800, color: '#ff7a2e' },
  { name: 'Legend', min: 600, color: '#ff945c' },
  { name: 'Based', min: 400, color: '#ffae85' },
  { name: 'Degen', min: 200, color: '#c66a2a' },
  { name: 'Normie', min: 100, color: '#8a5a3c' },
  { name: 'Anon', min: 0, color: '#3f3f46' }, // zinc-700 default
];

function tierForScore(score: number | null | undefined): string {
  const s = score ?? 0;
  for (const b of TIER_BANDS) {
    if (s >= b.min) return b.name;
  }
  return 'Anon';
}

// `searchUsers` returns a row shape that does NOT include tweetscout_score; in
// practice the admin search endpoint exposes a slim view. We're widening here
// to optimistically read the score if the backend starts including it — for now
// every user falls into Anon if the score isn't present, which still gives a
// readable donut driven by what the API actually returns.
type UserWithScore = AdminUserRow & { tweetscout_score?: number | null };

// Framer-motion row stagger — children appear 50ms apart in document order.
const ROW_VARIANTS = {
  hidden: { opacity: 0, y: 12 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { duration: 0.35, delay: i * 0.05, ease: [0.16, 1, 0.3, 1] as const },
  }),
};

type Range = 7 | 30 | 90;

// ---------- component ----------

export default function AdminDashboard() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUserRow[] | null>(null);
  const [engagementsSeries, setEngagementsSeries] = useState<TimeseriesResponse | null>(null);
  const [karmaSeries, setKarmaSeries] = useState<TimeseriesResponse | null>(null);
  const [karmaRange, setKarmaRange] = useState<Range>(30);
  const [karmaLoading, setKarmaLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Real display name from /me/ — the layout already gated the route so the
  // call always succeeds by the time this page renders. Duplicate call is
  // cheap (cached by the layout's initial fetch in most browsers).
  const [greetingName, setGreetingName] = useState<string>('');

  // Initial parallel load — stats + 30d karma + 200 users + who am I.
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      adminApi.getStats(),
      adminApi.getTimeseries('karma_earned', 30),
      adminApi.searchUsers('', 200),
      adminApi.me(),
    ])
      .then(([s, k, u, me]) => {
        if (cancelled) return;
        setStats(s);
        setKarmaSeries(k);
        setUsers(u);
        setGreetingName(me.telegram_username || (me.telegram_id ? `@${me.telegram_id}` : 'admin'));
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Engagements timeseries — independent of the karma range toggle; load
  // alongside the initial fetch but in a second effect so the karma toggle
  // refetch logic stays clean.
  useEffect(() => {
    let cancelled = false;
    adminApi
      .getTimeseries('engagements', 30)
      .then((r) => {
        if (cancelled) return;
        setEngagementsSeries(r);
      })
      .catch(() => {
        // Non-fatal — the main error banner already covers auth failures.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Refetch karma timeseries when the range toggle changes (skip the initial
  // mount because the first effect already fetched 30d).
  useEffect(() => {
    if (karmaRange === 30 && karmaSeries && karmaSeries.days === 30) {
      // initial load covered this case
      return;
    }
    let cancelled = false;
    setKarmaLoading(true);
    adminApi
      .getTimeseries('karma_earned', karmaRange)
      .then((r) => {
        if (cancelled) return;
        setKarmaSeries(r);
      })
      .catch(() => {
        // Non-fatal
      })
      .finally(() => {
        if (!cancelled) setKarmaLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // karmaSeries intentionally omitted — including it would re-fire on every
    // successful fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [karmaRange]);

  // Map actor_id -> telegram_username (best-effort; falls back to short UUID).
  const actorMap = useMemo(() => {
    const m = new Map<string, string>();
    if (!users) return m;
    for (const u of users) {
      if (u.telegram_username) m.set(u.id, u.telegram_username);
    }
    return m;
  }, [users]);

  // Compute tier distribution from the users list. Each user is placed in the
  // first band whose threshold their tweetscout_score meets. Bands with 0 users
  // are still included so colors stay stable across loads.
  const tierData = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const b of TIER_BANDS) counts[b.name] = 0;
    if (users) {
      for (const u of users as UserWithScore[]) {
        const t = tierForScore(u.tweetscout_score ?? 0);
        counts[t] = (counts[t] ?? 0) + 1;
      }
    }
    return TIER_BANDS.map((b) => ({
      name: b.name,
      value: counts[b.name] ?? 0,
      color: b.color,
    }));
  }, [users]);

  const tierTotal = useMemo(
    () => tierData.reduce((acc, d) => acc + d.value, 0),
    [tierData],
  );

  if (error) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="Couldn't load dashboard"
        description={
          error.startsWith('403')
            ? "You're authenticated but lack admin role. Bootstrap via backend/scripts/seed_admins.py (sets ADMIN_TELEGRAM_IDS to superadmin)."
            : error
        }
      />
    );
  }

  const loading = stats === null;
  const q = stats?.queues;
  const userStats = stats?.users;
  const c = stats?.credits;
  const p = stats?.posts;
  const e = stats?.engagements;
  const audit = stats?.recent_audit ?? [];

  return (
    <div className="grid grid-cols-12 gap-6">
      {/* ============================================================
          ROW 1 — Hero (full-width, gradient orange-tinted glass card)
          ============================================================ */}
      <motion.section
        custom={0}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12"
      >
        <div className="glass-card relative overflow-hidden p-8">
          {/* decorative orange wash on the right edge */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 w-1/2"
            style={{
              background:
                'radial-gradient(circle at 100% 50%, rgba(249,84,0,0.18) 0%, transparent 60%)',
            }}
          />
          <div className="relative flex flex-col items-start justify-between gap-6 lg:flex-row lg:items-center">
            <div className="min-w-0">
              <h1 className="font-syne text-2xl font-bold tracking-tight text-white">
                Welcome back{greetingName ? `, ${greetingName}` : ''}
              </h1>
              <p className="mt-1 text-sm text-zinc-400">
                You&rsquo;re watching{' '}
                <span className="font-semibold text-white tabular-nums">
                  {loading ? '…' : fmtInt(userStats?.total)}
                </span>{' '}
                users
              </p>
            </div>

            <div className="flex w-full flex-wrap gap-6 lg:w-auto lg:flex-nowrap lg:justify-end">
              <HeroMiniStat
                label="Engagements today"
                value={loading ? '—' : fmtInt(e?.today)}
              />
              <HeroMiniStat
                label="Active posts"
                value={loading ? '—' : fmtInt(p?.active)}
              />
              <HeroMiniStat
                label="Karma in circulation"
                value={loading ? '—' : fmtKarma(c?.in_circulation)}
              />
            </div>
          </div>
        </div>
      </motion.section>

      {/* ============================================================
          ROW 2 — Main karma chart (8) + sidebar quick stats (4)
          ============================================================ */}
      <motion.section
        custom={1}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12 lg:col-span-8"
      >
        <div className="glass-card relative overflow-hidden p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-xs font-medium uppercase tracking-wider text-zinc-500">
                Karma earned (last {karmaRange} days)
              </div>
              <div className="mt-3 flex items-baseline gap-3">
                <div className="stat-glow text-4xl font-bold leading-none tracking-tight text-white tabular-nums">
                  {karmaSeries ? fmtKarma(karmaSeries.total) : '—'}
                </div>
                <DeltaPill delta={karmaSeries?.delta_pct ?? null} />
              </div>
            </div>
            <RangeToggle value={karmaRange} onChange={setKarmaRange} />
          </div>

          <div className="mt-6">
            {karmaSeries && !karmaLoading ? (
              <AreaChart
                data={karmaSeries.points}
                height={260}
                valueFormatter={(n) => fmtKarma(n)}
              />
            ) : (
              <Skeleton className="h-[260px] w-full rounded-lg" />
            )}
          </div>
        </div>
      </motion.section>

      <motion.section
        custom={2}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12 lg:col-span-4"
      >
        <div className="flex h-full flex-col gap-4 rounded-2xl border border-white/[0.06] bg-[#0d0d0d] p-5">
          <div className="text-xs font-medium uppercase tracking-wider text-zinc-500">
            Action required
          </div>
          <div className="flex flex-col gap-1">
            <QueueRow
              label="Pending waitlist"
              value={loading ? '—' : fmtInt(q?.pending_waitlist)}
              tone={q && q.pending_waitlist > 0 ? 'warning' : 'default'}
              href="/admin/waitlist"
              icon={UserCheck}
            />
            <QueueRow
              label={`Pending X verifications`}
              value={loading ? '—' : fmtInt(q?.pending_x_verifications)}
              tone={q && q.pending_x_verifications > 0 ? 'warning' : 'default'}
              href="/admin/x-verification"
              icon={ShieldCheck}
            />
            <QueueRow
              label="Pending batches"
              value={loading ? '—' : fmtInt(q?.pending_batches)}
              tone={q && q.pending_batches > 0 ? 'warning' : 'default'}
              icon={Layers}
            />
          </div>

          <div className="mt-auto rounded-md bg-white/[0.02] px-3 py-2 text-[11px] text-zinc-500">
            Click any row with a pending count to triage.
          </div>
        </div>
      </motion.section>

      {/* ============================================================
          ROW 3 — Tier donut (6) + Engagements timeseries (6)
          ============================================================ */}
      <motion.section
        custom={3}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12 lg:col-span-6"
      >
        <div className="rounded-2xl border border-white/[0.06] bg-[#0d0d0d] p-6">
          <div className="flex items-start justify-between">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-zinc-500">
                Tier distribution
              </div>
              <div className="mt-1 text-[11px] text-zinc-600">
                Derived from TweetScout score bands
              </div>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-5 items-center gap-4">
            <div className="col-span-2">
              {users ? (
                <DonutChart data={tierData} total={tierTotal} label="users" height={200} />
              ) : (
                <Skeleton className="h-[200px] w-full rounded-full" />
              )}
            </div>

            <div className="col-span-3 flex flex-col gap-1">
              {tierData.map((d) => (
                <div
                  key={d.name}
                  className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-white/[0.02]"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className="inline-block h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: d.color }}
                    />
                    <span className="truncate text-xs text-zinc-400">{d.name}</span>
                  </div>
                  <span className="text-sm font-semibold tabular-nums text-white">
                    {fmtInt(d.value)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </motion.section>

      <motion.section
        custom={4}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12 lg:col-span-6"
      >
        <div className="rounded-2xl border border-white/[0.06] bg-[#0d0d0d] p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-xs font-medium uppercase tracking-wider text-zinc-500">
                Engagements (last 30 days)
              </div>
              <div className="mt-3 flex items-baseline gap-3">
                <div className="stat-glow text-3xl font-bold leading-none tracking-tight text-white tabular-nums">
                  {engagementsSeries ? fmtInt(engagementsSeries.total) : '—'}
                </div>
                <DeltaPill delta={engagementsSeries?.delta_pct ?? null} />
              </div>
            </div>
          </div>

          <div className="mt-6">
            {engagementsSeries ? (
              <AreaChart
                data={engagementsSeries.points}
                height={200}
                color="#f95400"
                valueFormatter={(n) => n.toLocaleString()}
              />
            ) : (
              <Skeleton className="h-[200px] w-full rounded-lg" />
            )}
          </div>
        </div>
      </motion.section>

      {/* ============================================================
          ROW 4 — Recent activity (8) + Queues compact (4)
          ============================================================ */}
      <motion.section
        custom={5}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12 lg:col-span-8"
      >
        <div className="rounded-2xl border border-white/[0.06] bg-[#0d0d0d]">
          <div className="flex items-center justify-between border-b border-white/[0.04] px-5 py-3">
            <div className="text-xs font-medium uppercase tracking-wider text-zinc-500">
              Recent admin activity
            </div>
            <div className="text-[11px] text-zinc-600">last 10 events</div>
          </div>

          {loading ? (
            <div className="p-4">
              <TableSkeleton rows={6} />
            </div>
          ) : audit.length === 0 ? (
            <div className="p-4">
              <EmptyState icon={Activity} title="No recent admin activity" />
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-[10px] uppercase tracking-wide text-zinc-600">
                <tr>
                  <th className="px-5 py-2 font-medium">When</th>
                  <th className="px-5 py-2 font-medium">Action</th>
                  <th className="px-5 py-2 font-medium">Actor</th>
                  <th className="px-5 py-2 font-medium">Target</th>
                  <th className="px-5 py-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {audit.slice(0, 10).map((row, i) => {
                  const actorName = row.actor_id ? actorMap.get(row.actor_id) : null;
                  const actorDisplay = actorName
                    ? `@${actorName}`
                    : row.actor_id
                      ? shortId(row.actor_id)
                      : 'system';
                  return (
                    <tr
                      key={row.id}
                      className={cn(
                        'transition-colors hover:bg-white/[0.02]',
                        i % 2 === 1 && 'bg-white/[0.01]',
                      )}
                    >
                      <td className="whitespace-nowrap px-5 py-2.5 text-xs text-zinc-500">
                        {relativeTime(row.created_at_iso)}
                      </td>
                      <td className="px-5 py-2.5">
                        <Badge tone={actionTone(row.action)}>{row.action}</Badge>
                      </td>
                      <td className="whitespace-nowrap px-5 py-2.5 text-xs text-zinc-300">
                        {actorDisplay}
                      </td>
                      <td className="whitespace-nowrap px-5 py-2.5 text-xs text-zinc-400">
                        <span className="text-zinc-500">{row.target_type}</span>
                        {row.target_id && (
                          <>
                            <span className="mx-1 text-zinc-700">/</span>
                            <code className="rounded bg-black/40 px-1 text-[11px]">
                              {shortId(row.target_id)}
                            </code>
                          </>
                        )}
                      </td>
                      <td className="px-5 py-2.5 text-[11px] text-zinc-500">
                        <code className="break-all">{detailSummary(row.detail)}</code>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </motion.section>

      <motion.section
        custom={6}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12 lg:col-span-4"
      >
        <div className="flex h-full flex-col gap-3">
          <QueueCard
            label="Waitlist"
            value={loading ? '—' : fmtInt(q?.pending_waitlist)}
            href="/admin/waitlist"
            icon={UserCheck}
            tone={q && q.pending_waitlist > 0 ? 'warning' : 'default'}
          />
          <QueueCard
            label="X verifications"
            value={loading ? '—' : fmtInt(q?.pending_x_verifications)}
            href="/admin/x-verification"
            icon={ShieldCheck}
            tone={q && q.pending_x_verifications > 0 ? 'warning' : 'default'}
          />
          <QueueCard
            label="Batches"
            value={loading ? '—' : fmtInt(q?.pending_batches)}
            icon={Layers}
            tone={q && q.pending_batches > 0 ? 'warning' : 'default'}
          />
        </div>
      </motion.section>

      {/* ============================================================
          ROW 5 — Users overview (full width, horizontal strip)
          ============================================================ */}
      <motion.section
        custom={7}
        initial="hidden"
        animate="visible"
        variants={ROW_VARIANTS}
        className="col-span-12"
      >
        <div className="rounded-2xl border border-white/[0.06] bg-[#0d0d0d] p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-syne text-base font-semibold tracking-tight text-white">Users</h2>
            <Link
              href="/admin/users"
              className="inline-flex items-center gap-1 text-[11px] uppercase tracking-wider text-zinc-500 hover:text-white"
            >
              All users <ArrowUpRight size={11} />
            </Link>
          </div>

          <div className="-mx-2 overflow-x-auto px-2">
            <div className="flex min-w-max gap-2">
              <UserStat label="Total" value={loading ? '—' : fmtInt(userStats?.total)} />
              <UserStat label="Admins" value={loading ? '—' : fmtInt(userStats?.by_role.admin)} />
              <UserStat
                label="Superadmins"
                value={loading ? '—' : fmtInt(userStats?.by_role.superadmin)}
              />
              <UserStat label="Regular" value={loading ? '—' : fmtInt(userStats?.by_role.regular)} />
              <UserStat
                label="Banned"
                value={loading ? '—' : fmtInt(userStats?.banned)}
                tone={userStats && userStats.banned > 0 ? 'danger' : 'default'}
              />
              <UserStat
                label="Whitelisted"
                value={loading ? '—' : fmtInt(userStats?.whitelisted)}
                tone="success"
              />
              <UserStat label="X-Verified" value={loading ? '—' : fmtInt(userStats?.x_verified)} />
              <UserStat
                label="New this week"
                value={loading ? '—' : fmtInt(userStats?.new_this_week)}
              />
            </div>
          </div>
        </div>
      </motion.section>
    </div>
  );
}

// ---------- inline sub-components ----------

function HeroMiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <div className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">{label}</div>
      <div className="mt-1 text-xl font-bold leading-none tabular-nums text-white">
        {value}
      </div>
    </div>
  );
}

function DeltaPill({ delta }: { delta: number | null }) {
  if (delta === null || delta === undefined) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[11px] font-medium text-zinc-500">
        no prior data
      </span>
    );
  }
  if (delta > 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-900/50 bg-emerald-950/40 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
        +{delta.toFixed(1)}%
      </span>
    );
  }
  if (delta < 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-red-900/50 bg-red-950/40 px-2 py-0.5 text-[11px] font-semibold text-red-300">
        {delta.toFixed(1)}%
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[11px] font-medium text-zinc-400">
      0%
    </span>
  );
}

function RangeToggle({ value, onChange }: { value: Range; onChange: (r: Range) => void }) {
  const opts: Range[] = [7, 30, 90];
  return (
    <div className="inline-flex rounded-md border border-white/[0.08] bg-white/[0.02] p-0.5 text-[11px]">
      {opts.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={cn(
            'rounded px-2 py-1 font-medium tabular-nums transition-colors',
            value === o
              ? 'bg-white/[0.08] text-white'
              : 'text-zinc-500 hover:text-zinc-300',
          )}
        >
          {o}d
        </button>
      ))}
    </div>
  );
}

// QueueRow is a MetricRow-style line but wraps in a Link when href is provided.
// MetricRow itself has no href prop, so this is the link-aware wrapper sketched
// in the task spec ("with link").
function QueueRow({
  label,
  value,
  tone,
  href,
  icon,
}: {
  label: string;
  value: string;
  tone?: 'default' | 'warning';
  href?: string;
  icon?: typeof UserCheck;
}) {
  const mappedTone = tone === 'warning' ? 'warning' : 'default';
  const inner = <MetricRow label={label} value={value} tone={mappedTone} icon={icon} />;
  if (!href) return inner;
  return (
    <Link href={href} className="block rounded-md transition-colors hover:bg-white/[0.03]">
      {inner}
    </Link>
  );
}

// A compact stacked card used in row 4 — icon + label + count + (optional) link.
function QueueCard({
  label,
  value,
  href,
  icon: Icon,
  tone = 'default',
}: {
  label: string;
  value: string;
  href?: string;
  icon: typeof UserCheck;
  tone?: 'default' | 'warning';
}) {
  const iconTone =
    tone === 'warning' ? 'bg-amber-950/40 text-amber-400' : 'bg-white/[0.04] text-zinc-400';
  const valueTone = tone === 'warning' ? 'text-amber-300' : 'text-white';
  const content = (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-white/[0.06] bg-[#0d0d0d] p-4 transition-colors hover:border-white/[0.14] hover:bg-[#111]">
      <div className="flex min-w-0 items-center gap-3">
        <div className={cn('rounded-md p-2', iconTone)}>
          <Icon size={16} />
        </div>
        <div className="min-w-0">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
            {label}
          </div>
          <div className={cn('mt-0.5 text-xl font-bold tabular-nums', valueTone)}>
            {value}
          </div>
        </div>
      </div>
      {href && <ArrowUpRight size={14} className="text-zinc-600" />}
    </div>
  );
  return href ? (
    <Link href={href} className="block">
      {content}
    </Link>
  ) : (
    content
  );
}

function UserStat({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: string;
  tone?: 'default' | 'success' | 'danger';
}) {
  const TONE_VALUE: Record<typeof tone, string> = {
    default: 'text-white',
    success: 'text-emerald-300',
    danger: 'text-red-300',
  };
  return (
    <div className="min-w-[140px] rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3">
      <div className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">{label}</div>
      <div
        className={cn(
          'mt-1.5 text-xl font-bold leading-none tabular-nums',
          TONE_VALUE[tone],
        )}
      >
        {value}
      </div>
    </div>
  );
}
