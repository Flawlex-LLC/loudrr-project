'use client';

/**
 * Admin → Operations: what breaks in launch week, made visible and fixable
 * without a database console.
 *
 *  Health         gateway credits, sponsor feed, held/failed claims, failed notifications
 *  Claims         verification batches — requeue a stuck or FAILED one (the sweeper never retries `failed`)
 *  Notifications  Telegram messages — re-send a failed one (auto-retry gives up after 24h)
 *  Audit log      every admin action, filterable
 *  Posts          pull a bad post from Engage and refund its escrow
 */
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { toast } from 'sonner';
import { Activity, ExternalLink, RefreshCcw, RotateCcw, Search, XCircle } from 'lucide-react';

import {
  adminApi,
  type OpsAuditRow,
  type OpsBatch,
  type OpsHealth,
  type OpsNotification,
  type OpsPage,
  type OpsPost,
} from '@/lib/api';
import { useAdminResource } from '@/lib/admin/useAdminResource';
import { RequiresRole } from '@/lib/admin/session';
import { absolute, karma, num, timeAgo } from '@/lib/admin/format';
import { Button } from '@/components/admin/Button';
import { Badge } from '@/components/admin/Badge';
import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { EmptyState } from '@/components/admin/EmptyState';
import { Pagination } from '@/components/admin/Pagination';
import { StatusStrip } from '@/components/admin/StatusStrip';
import { TableScroll } from '@/components/admin/TableScroll';
import { TableSkeleton } from '@/components/admin/Skeleton';
import { cn } from '@/lib/utils';

const TABS = [
  { key: 'health', label: 'Health' },
  { key: 'claims', label: 'Claims' },
  { key: 'notifications', label: 'Notifications' },
  { key: 'audit', label: 'Audit log' },
  { key: 'posts', label: 'Posts' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

const PAGE_SIZE = 25;

function errorText(e: unknown): string {
  return String((e as Error)?.message || e || 'Request failed').replace(/^\d{3}:\s*/, '');
}

function LoadError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-200">
      <span>{error}</span>
      <Button variant="secondary" size="sm" onClick={onRetry}>Try again</Button>
    </div>
  );
}

// ------------------------------------------------------------------ health
function HealthTab({ onJump }: { onJump: (tab: TabKey) => void }) {
  const { data, loading, error, reload } = useAdminResource<OpsHealth>(() => adminApi.opsHealth(), []);
  if (error && !data) return <LoadError error={error} onRetry={reload} />;
  if (!data) return <TableSkeleton rows={4} />;

  const g = data.gateway;
  const b = data.batches;
  const lastPollMin = data.sponsor_feed.minutes_since_poll;
  const feedStale = data.sponsor_feed.active_sponsors > 0 && (lastPollMin === null || lastPollMin > 5);

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="secondary" size="sm" onClick={reload} disabled={loading}>
          <RefreshCcw size={12} /> Refresh
        </Button>
      </div>
      <StatusStrip
        items={[
          {
            label: 'X gateway',
            value: !g.configured ? 'Not configured' : g.reachable ? `${num(g.credits ?? 0)} credits` : 'Unreachable',
            tone: !g.configured || !g.reachable ? 'bad' : (g.credits ?? 0) < 50_000 ? 'warn' : 'ok',
            hint: g.error || 'Reply verification and sponsor posts both run on this key. Top it up before it hits 0.',
          },
          {
            label: 'Sponsor feed',
            value: !data.sponsor_feed.stream_enabled
              ? 'Off on this server'
              : lastPollMin === null ? 'No poll yet' : `Last poll ${lastPollMin}m ago`,
            tone: feedStale ? 'warn' : 'ok',
            hint: `${data.sponsor_feed.active_sponsors} active sponsor account(s). The worker polls every ${data.sponsor_feed.poll_seconds}s.`,
          },
          {
            label: 'Held claims',
            value: `${b.held} held · ${b.failed} failed`,
            tone: b.held > 0 || b.failed > 0 ? 'bad' : 'ok',
            hint: b.oldest_waiting_minutes !== null
              ? `Oldest waiting claim: ${b.oldest_waiting_minutes} min. Held usually means the gateway refused our key.`
              : 'No claims waiting.',
          },
          {
            label: 'Notifications',
            value: `${data.outbox.failed} failed`,
            tone: data.outbox.failed > 0 ? 'warn' : 'ok',
            hint: 'Telegram messages that never arrived (bans, approvals, rejections).',
          },
        ]}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => onJump('claims')}
          className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 text-left hover:border-[#f95400]/40"
        >
          <div className="text-sm font-semibold text-white">Claims waiting: {b.pending + b.processing}</div>
          <div className="mt-1 text-xs text-zinc-400">
            {b.held} held over 15 min, {b.failed_24h} failed in the last 24h. Open to requeue.
          </div>
        </button>
        <button
          type="button"
          onClick={() => onJump('notifications')}
          className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 text-left hover:border-[#f95400]/40"
        >
          <div className="text-sm font-semibold text-white">Failed notifications: {data.outbox.failed}</div>
          <div className="mt-1 text-xs text-zinc-400">Open to see why and re-send.</div>
        </button>
      </div>
      <p className="text-xs text-zinc-500">Gateway balance checked {timeAgo(g.checked_at)} (cached for a minute).</p>
    </div>
  );
}

// ------------------------------------------------------------------ claims
const BATCH_FILTERS = [
  { key: 'held', label: 'Held' },
  { key: 'failed', label: 'Failed' },
  { key: 'pending', label: 'Pending' },
  { key: 'processing', label: 'Processing' },
  { key: 'completed', label: 'Completed' },
  { key: '', label: 'All' },
];

function ClaimsTab() {
  const [status, setStatus] = useState('held');
  const [page, setPage] = useState(1);
  const [target, setTarget] = useState<OpsBatch | null>(null);
  const { data, error, reload } = useAdminResource<OpsPage<OpsBatch>>(
    () => adminApi.opsBatches(status, PAGE_SIZE, (page - 1) * PAGE_SIZE), [status, page],
  );

  return (
    <div className="space-y-4">
      <FilterChips value={status} options={BATCH_FILTERS} onChange={(v) => { setStatus(v); setPage(1); }} />
      {error && <LoadError error={error} onRetry={reload} />}
      {!data ? <TableSkeleton rows={5} /> : data.items.length === 0 ? (
        <EmptyState icon={Activity} title="Nothing here" description="No claim batches match this filter." />
      ) : (
        <>
          <TableScroll minWidth={760}>
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-zinc-400">
                <tr>
                  <th className="px-3 py-2">User</th><th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Engagements</th><th className="px-3 py-2">Result</th>
                  <th className="px-3 py-2">Waiting</th><th className="px-3 py-2">Message</th>
                  <th className="px-3 py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.05]">
                {data.items.map((b) => (
                  <tr key={b.id}>
                    <td className="px-3 py-2 text-white">{b.user_handle ? `@${b.user_handle}` : b.user_id.slice(0, 8)}</td>
                    <td className="px-3 py-2">
                      <Badge tone={b.status === 'failed' ? 'danger' : b.held ? 'warning' : b.status === 'completed' ? 'success' : 'neutral'}>
                        {b.held ? `held · ${b.status}` : b.status}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 tabular-nums">{b.engagements}</td>
                    <td className="px-3 py-2 tabular-nums text-zinc-300">
                      {b.passed === null ? '—' : `${b.passed} ok / ${b.failed ?? 0} failed`}
                      {b.credits_awarded ? ` · ${karma(b.credits_awarded)}` : ''}
                    </td>
                    <td className="px-3 py-2 text-zinc-300" title={absolute(b.created_at)}>{timeAgo(b.created_at)}</td>
                    <td className="max-w-[260px] truncate px-3 py-2 text-zinc-400" title={b.message}>{b.message || '—'}</td>
                    <td className="px-3 py-2 text-right">
                      {b.status !== 'completed' && (
                        <RequiresRole role="superadmin">
                          <Button size="sm" variant="secondary" onClick={() => setTarget(b)} aria-label={`Requeue claim batch of ${b.user_handle || b.user_id}`}>
                            <RotateCcw size={12} /> Requeue
                          </Button>
                        </RequiresRole>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </>
      )}
      <ConfirmDialog
        open={!!target}
        onClose={() => setTarget(null)}
        title="Requeue this claim?"
        description={target ? `@${target.user_handle || target.user_id.slice(0, 8)}'s ${target.engagements} engagements go back through verification. Nothing is paid twice: already-settled engagements are skipped.` : ''}
        confirmLabel="Requeue"
        onConfirm={async () => {
          if (!target) return;
          try {
            const r = await adminApi.opsRequeueBatch(target.id);
            toast.success('Claim requeued', {
              description: r.queued ? 'The worker picks it up now.' : 'Marked pending — the worker will pick it up.',
            });
            setTarget(null);
            await reload();
          } catch (e) {
            toast.error("Couldn't requeue", { description: errorText(e) });
          }
        }}
      />
    </div>
  );
}

// ------------------------------------------------------------------ notifications
const NOTIF_FILTERS = [
  { key: 'failed', label: 'Failed' },
  { key: 'pending', label: 'Pending' },
  { key: 'sent', label: 'Sent' },
  { key: '', label: 'All' },
];

function NotificationsTab() {
  const [status, setStatus] = useState('failed');
  const [page, setPage] = useState(1);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { data, error, reload } = useAdminResource<OpsPage<OpsNotification>>(
    () => adminApi.opsOutbox(status, PAGE_SIZE, (page - 1) * PAGE_SIZE), [status, page],
  );

  async function retry(n: OpsNotification) {
    setBusyId(n.id);
    try {
      await adminApi.opsRetryNotification(n.id);
      toast.success('Queued to re-send', { description: n.event_type.replaceAll('_', ' ') });
      await reload();
    } catch (e) {
      toast.error("Couldn't re-send", { description: errorText(e) });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <FilterChips value={status} options={NOTIF_FILTERS} onChange={(v) => { setStatus(v); setPage(1); }} />
      {error && <LoadError error={error} onRetry={reload} />}
      {!data ? <TableSkeleton rows={5} /> : data.items.length === 0 ? (
        <EmptyState title="Nothing here" description={status === 'failed' ? 'No failed notifications. Every message was delivered.' : 'No notifications match this filter.'} />
      ) : (
        <>
          <TableScroll minWidth={720}>
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-zinc-400">
                <tr>
                  <th className="px-3 py-2">Message</th><th className="px-3 py-2">To</th>
                  <th className="px-3 py-2">Status</th><th className="px-3 py-2">Why it failed</th>
                  <th className="px-3 py-2">Created</th><th className="px-3 py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.05]">
                {data.items.map((n) => (
                  <tr key={n.id}>
                    <td className="px-3 py-2 text-white">{n.event_type.replaceAll('_', ' ')}</td>
                    <td className="px-3 py-2 tabular-nums text-zinc-300">{n.telegram_id ?? '—'}</td>
                    <td className="px-3 py-2">
                      <Badge tone={n.status === 'failed' ? 'danger' : n.status === 'sent' ? 'success' : 'neutral'}>
                        {n.status}{n.status === 'failed' ? ` · ${n.retry_count}/${n.max_retries}` : ''}
                      </Badge>
                    </td>
                    <td className="max-w-[280px] truncate px-3 py-2 text-zinc-400" title={n.error_message}>{n.error_message || '—'}</td>
                    <td className="px-3 py-2 text-zinc-300" title={absolute(n.created_at)}>{timeAgo(n.created_at)}</td>
                    <td className="px-3 py-2 text-right">
                      {n.status === 'failed' && (
                        <Button size="sm" variant="secondary" loading={busyId === n.id} onClick={() => retry(n)}>
                          <RotateCcw size={12} /> Re-send
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ audit
function AuditTab() {
  const [action, setAction] = useState('');
  const [actorInput, setActorInput] = useState('');
  const [actor, setActor] = useState('');
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const size = 50;
  const { data, error, reload } = useAdminResource<OpsPage<OpsAuditRow> & { actions: string[] }>(
    () => adminApi.opsAudit({ action, actor, limit: size, offset: (page - 1) * size }), [action, actor, page],
  );

  return (
    <div className="space-y-4">
      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => { e.preventDefault(); setActor(actorInput.trim()); setPage(1); }}
      >
        <select
          value={action}
          onChange={(e) => { setAction(e.target.value); setPage(1); }}
          className="rounded-lg border border-white/[0.08] bg-[#0a0a0a] px-3 py-2 text-sm text-white"
          aria-label="Filter by action"
        >
          <option value="">All actions</option>
          {(data?.actions || []).map((a) => <option key={a} value={a}>{a.replaceAll('_', ' ')}</option>)}
        </select>
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            value={actorInput}
            onChange={(e) => setActorInput(e.target.value)}
            placeholder="Admin @handle"
            aria-label="Filter by admin handle"
            className="w-full rounded-lg border border-white/[0.08] bg-[#0a0a0a] py-2 pl-9 pr-3 text-sm text-white placeholder:text-zinc-500"
          />
        </div>
        <Button type="submit" variant="secondary">Filter</Button>
      </form>
      {error && <LoadError error={error} onRetry={reload} />}
      {!data ? <TableSkeleton rows={6} /> : data.items.length === 0 ? (
        <EmptyState title="No actions found" description="Nothing in the audit trail matches these filters." />
      ) : (
        <>
          <div className="divide-y divide-white/[0.05] rounded-xl border border-white/[0.08]">
            {data.items.map((a) => (
              <div key={a.id} className="p-3">
                <button
                  type="button"
                  onClick={() => setOpen(open === a.id ? null : a.id)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left"
                  aria-expanded={open === a.id}
                >
                  <span className="font-medium text-white">{a.action.replaceAll('_', ' ')}</span>
                  <span className="text-xs text-zinc-400">by {a.actor_handle ? `@${a.actor_handle}` : a.actor_id ? a.actor_id.slice(0, 8) : 'system'}</span>
                  <span className="ml-auto text-xs text-zinc-400" title={absolute(a.created_at)}>{timeAgo(a.created_at)}</span>
                </button>
                {open === a.id && (
                  <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 p-3 text-xs text-zinc-300">
                    {JSON.stringify({ target: a.target_type, target_id: a.target_id, ...a.detail }, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
          <Pagination page={page} pageSize={size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ posts
const POST_FILTERS = [
  { key: 'active', label: 'Live' },
  { key: 'completed', label: 'Completed' },
  { key: 'cancelled', label: 'Cancelled' },
  { key: '', label: 'All' },
];

function PostsTab() {
  const [status, setStatus] = useState('active');
  const [qInput, setQInput] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [target, setTarget] = useState<OpsPost | null>(null);
  const [reason, setReason] = useState('');
  const { data, error, reload } = useAdminResource<OpsPage<OpsPost>>(
    () => adminApi.opsPosts(status, q, PAGE_SIZE, (page - 1) * PAGE_SIZE), [status, q, page],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <FilterChips value={status} options={POST_FILTERS} onChange={(v) => { setStatus(v); setPage(1); }} />
        <form className="flex flex-1 gap-2" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); setPage(1); }}>
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="@author, text or link"
            aria-label="Search posts"
            className="w-full rounded-lg border border-white/[0.08] bg-[#0a0a0a] px-3 py-2 text-sm text-white placeholder:text-zinc-500"
          />
          <Button type="submit" variant="secondary">Search</Button>
        </form>
      </div>
      {error && <LoadError error={error} onRetry={reload} />}
      {!data ? <TableSkeleton rows={5} /> : data.items.length === 0 ? (
        <EmptyState title="No posts found" description="Nothing matches this filter." />
      ) : (
        <>
          <div className="space-y-2">
            {data.items.map((p) => (
              <div key={p.id} className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-white">{p.author ? `@${p.author}` : 'post'}</span>
                  {p.is_sponsored && <Badge tone="info">sponsored</Badge>}
                  <Badge tone={p.status === 'active' ? 'success' : p.status === 'cancelled' ? 'danger' : 'neutral'}>{p.status}</Badge>
                  <span className="text-xs text-zinc-400">by {p.is_sponsored ? 'the platform' : p.poster_handle ? `@${p.poster_handle}` : p.poster_id.slice(0, 8)}</span>
                  <span className="ml-auto text-xs text-zinc-400" title={absolute(p.created_at)}>{timeAgo(p.created_at)}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-zinc-300">{p.tweet_text || '—'}</p>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-zinc-400">
                  <span className="tabular-nums">{karma(p.escrow)} / {karma(p.initial_escrow)} escrow left</span>
                  <span className="tabular-nums">{num(p.engagements)} engagements</span>
                  <a href={p.x_link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[#f95400] hover:underline">
                    Open on X <ExternalLink size={11} />
                  </a>
                  {p.status === 'active' && (
                    <span className="ml-auto">
                      <RequiresRole role="superadmin">
                        <Button size="sm" variant="danger" onClick={() => { setReason(''); setTarget(p); }} aria-label={`Cancel post by ${p.author || p.poster_handle || p.id}`}>
                          <XCircle size={12} /> Cancel post
                        </Button>
                      </RequiresRole>
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </>
      )}
      <ConfirmDialog
        open={!!target}
        onClose={() => setTarget(null)}
        tone="danger"
        title="Cancel this post?"
        description={target ? (target.is_sponsored
          ? 'It disappears from Engage now. Sponsored escrow is platform karma, so nothing is refunded.'
          : `It disappears from Engage now and ${karma(target.escrow)} karma goes back to the poster. Engagers who already claimed keep their karma.`) : ''}
        details={(
          <label className="block text-xs text-zinc-400">
            Reason (audit log only — not sent to anyone)
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              rows={2}
              data-autofocus
              className="mt-1 w-full rounded-lg border border-white/[0.08] bg-[#0a0a0a] p-2 text-sm text-white"
              placeholder="e.g. phishing link, tweet deleted"
            />
          </label>
        )}
        confirmLabel="Cancel post"
        onConfirm={async () => {
          if (!target) return;
          try {
            const r = await adminApi.opsCancelPost(target.id, reason);
            toast.success('Post cancelled', {
              description: r.refunded ? `${karma(r.refunded)} karma refunded to the poster.` : 'No refund (sponsored post).',
            });
            setTarget(null);
            await reload();
          } catch (e) {
            toast.error("Couldn't cancel the post", { description: errorText(e) });
          }
        }}
      />
    </div>
  );
}

// ------------------------------------------------------------------ shared bits
function FilterChips({ value, options, onChange }: {
  value: string;
  options: { key: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Filter">
      {options.map((o) => (
        <button
          key={o.key || 'all'}
          type="button"
          onClick={() => onChange(o.key)}
          aria-pressed={value === o.key}
          className={cn(
            'rounded-lg border px-3 py-1.5 text-xs font-medium',
            value === o.key
              ? 'border-[#f95400]/50 bg-[#f95400]/15 text-white'
              : 'border-white/[0.08] text-zinc-300 hover:border-white/20',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function OpsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const initial = (params.get('tab') as TabKey) || 'health';
  const [tab, setTab] = useState<TabKey>(TABS.some((t) => t.key === initial) ? initial : 'health');

  useEffect(() => {
    const current = params.get('tab') || 'health';
    if (current !== tab) router.replace(tab === 'health' ? '/admin/ops' : `/admin/ops?tab=${tab}`, { scroll: false });
  }, [tab, params, router]);

  const jump = useCallback((t: TabKey) => setTab(t), []);
  const body = useMemo(() => {
    switch (tab) {
      case 'claims': return <ClaimsTab />;
      case 'notifications': return <NotificationsTab />;
      case 'audit': return <AuditTab />;
      case 'posts': return <PostsTab />;
      default: return <HealthTab onJump={jump} />;
    }
  }, [tab, jump]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-syne text-2xl font-bold tracking-tight">Operations</h1>
        <p className="mt-1 text-sm text-zinc-400">Stuck claims, failed messages, the audit trail and post moderation.</p>
      </div>
      <div className="flex gap-1 overflow-x-auto border-b border-white/[0.08]" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            type="button"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium',
              tab === t.key ? 'border-[#f95400] text-white' : 'border-transparent text-zinc-400 hover:text-white',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      {body}
    </div>
  );
}

export default function OpsPage() {
  return (
    <Suspense fallback={null}>
      <OpsInner />
    </Suspense>
  );
}
