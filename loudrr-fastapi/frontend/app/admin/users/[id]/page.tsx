'use client';

/**
 * One user, in full — the view that has to exist before an admin is allowed
 * to move someone's balance.
 *
 * Overview shows identity, flags, score/tier, the credit block (including
 * `spendable_headroom`, so a divergence between the balance and the lifetime
 * earned-minus-spent room is visible rather than mysterious), XP, activity,
 * the waitlist entry and referrals. The other four tabs are the histories:
 * ledger, posts, engagements, and the admin actions taken on this account.
 */

import { use, useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ArrowLeft,
  Ban,
  CheckCircle2,
  Crown,
  ExternalLink,
  Minus,
  Plus,
  RefreshCcw,
  Shield,
  ShieldOff,
  UserCheck,
} from 'lucide-react';

import {
  adminApi,
  type AdminUserAuditRow,
  type AdminUserDetail,
  type AdminUserEngagement,
  type AdminUserPost,
  type AdminUserTransaction,
} from '@/lib/api';
import { Badge } from '@/components/admin/Badge';
import { Button } from '@/components/admin/Button';
import { CopyButton } from '@/components/admin/CopyButton';
import { EmptyState } from '@/components/admin/EmptyState';
import { Pagination } from '@/components/admin/Pagination';
import { TableScroll } from '@/components/admin/TableScroll';
import { Skeleton, TableSkeleton } from '@/components/admin/Skeleton';
import { RequiresRole, useAdminSession } from '@/lib/admin/session';
import { useAdminResource } from '@/lib/admin/useAdminResource';
import { absolute, karma, num, timeAgo } from '@/lib/admin/format';
import { cn } from '@/lib/utils';
import {
  identityLabel,
  newRequestId,
  UserActionDialog,
  type ActionTarget,
  type UserAction,
} from '../UserActionDialog';

const TABS = ['Overview', 'Ledger', 'Posts', 'Engagements', 'Audit'] as const;
type Tab = (typeof TABS)[number];

const SUB_PAGE_SIZE = 25;

export default function AdminUserDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { me } = useAdminSession();
  const [tab, setTab] = useState<Tab>('Overview');
  // minted in the click handler so a grant/revoke is idempotent on retry
  const [action, setAction] = useState<{ kind: UserAction; requestId: string } | null>(null);
  const open = (kind: UserAction) => setAction({ kind, requestId: newRequestId() });

  const { data: user, loading, error, reload } = useAdminResource(
    () => adminApi.getUser(id),
    [id],
  );

  const onDone = useCallback(async () => { await reload(); }, [reload]);
  const isSelf = me?.id === id;

  if (error && !user) {
    return (
      <div className="space-y-4">
        <BackLink />
        <EmptyState
          icon={ShieldOff}
          title="Couldn't load this user"
          description={error}
          action={<Button variant="secondary" size="sm" onClick={reload}>Retry</Button>}
        />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="space-y-4">
        <BackLink />
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-40" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28 w-full" />)}
        </div>
      </div>
    );
  }

  const name = identityLabel(user);

  return (
    <div className="space-y-6">
      <BackLink />

      {/* ---- header ---- */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-syne text-2xl font-bold tracking-tight">
            {name}
            {isSelf && <span className="ml-2 align-middle text-[11px] uppercase text-[#f95400]">you</span>}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
            <span className="font-mono">tg {user.telegram_id ?? '—'}</span>
            <span className="inline-flex items-center gap-1 font-mono">
              {user.id}
              <CopyButton value={user.id} label="Copy user id" />
            </span>
            <span>joined {timeAgo(user.created_at)}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {user.flags.role === 'superadmin' && <Badge tone="superadmin"><Crown size={9} aria-hidden />superadmin</Badge>}
            {user.flags.role === 'admin' && <Badge tone="admin"><Shield size={9} aria-hidden />admin</Badge>}
            {user.flags.is_banned && <Badge tone="danger">banned</Badge>}
            {user.flags.is_whitelisted && <Badge tone="warning">whitelisted</Badge>}
            {user.flags.x_verified && <Badge tone="success"><CheckCircle2 size={9} aria-hidden />x-verified</Badge>}
            {user.flags.pending_x_verification && <Badge tone="info">x-verification pending</Badge>}
            {user.flags.loud_access && <Badge tone="info">loud access</Badge>}
          </div>
        </div>

        {/* actions live NEXT TO the numbers they change */}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
            <RefreshCcw size={12} aria-hidden />
            Refresh
          </Button>
          <Button size="sm" variant="success" aria-label={`Grant karma to ${name}`} onClick={() => open('grant')}>
            <Plus size={12} aria-hidden />
            Grant
          </Button>
          <RequiresRole role="superadmin">
            <Button
              size="sm"
              variant="secondary"
              disabled={isSelf}
              aria-label={`Revoke karma from ${name}`}
              onClick={() => open('revoke')}
            >
              <Minus size={12} aria-hidden />
              Revoke
            </Button>
          </RequiresRole>
          <RequiresRole role="superadmin">
            <Button
              size="sm"
              variant="secondary"
              aria-label={user.flags.is_whitelisted ? `Remove ${name} from the whitelist` : `Whitelist ${name}`}
              onClick={() => open(user.flags.is_whitelisted ? 'whitelist-off' : 'whitelist-on')}
            >
              <UserCheck size={12} aria-hidden />
              {user.flags.is_whitelisted ? 'Un-whitelist' : 'Whitelist'}
            </Button>
          </RequiresRole>
          {user.flags.is_banned ? (
            <Button size="sm" variant="secondary" aria-label={`Unban ${name}`} onClick={() => open('unban')}>
              <UserCheck size={12} aria-hidden />
              Unban
            </Button>
          ) : (
            <Button
              size="sm"
              variant="danger"
              disabled={isSelf}
              title={isSelf ? 'You cannot ban your own account' : undefined}
              aria-label={`Ban ${name}`}
              onClick={() => open('ban')}
            >
              <Ban size={12} aria-hidden />
              Ban
            </Button>
          )}
        </div>
      </header>

      {/* ---- tabs ---- */}
      <div className="border-b border-white/[0.06]" role="tablist" aria-label="User detail sections">
        <div className="-mb-px flex gap-1 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={cn(
                'whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors',
                tab === t
                  ? 'border-[#f95400] text-white'
                  : 'border-transparent text-zinc-500 hover:text-zinc-300',
              )}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div role="tabpanel" aria-label={tab}>
        {tab === 'Overview' && <Overview user={user} />}
        {tab === 'Ledger' && <LedgerTab userId={id} />}
        {tab === 'Posts' && <PostsTab userId={id} />}
        {tab === 'Engagements' && <EngagementsTab userId={id} />}
        {tab === 'Audit' && <AuditTab userId={id} />}
      </div>

      {/* keyed per opening so the draft fields reset by remounting */}
      <UserActionDialog
        key={action ? `${action.kind}-${action.requestId}` : 'idle'}
        action={action?.kind ?? null}
        target={toTarget(user)}
        requestId={action?.requestId ?? ''}
        onClose={() => setAction(null)}
        onDone={onDone}
      />
    </div>
  );
}

function toTarget(u: AdminUserDetail): ActionTarget {
  return {
    id: u.id,
    telegram_id: u.telegram_id,
    telegram_username: u.telegram_username,
    x_username: u.x_username,
    display_name: u.display_name,
    credits: u.credits.balance,
    role: u.flags.role,
    is_banned: u.flags.is_banned,
    is_whitelisted: u.flags.is_whitelisted,
  };
}

function BackLink() {
  return (
    <Link
      href="/admin/users"
      className="inline-flex items-center gap-1.5 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
    >
      <ArrowLeft size={13} aria-hidden />
      All users
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
function Overview({ user }: { user: AdminUserDetail }) {
  const c = user.credits;
  // Balance above the lifetime earned-minus-spent room can't be spent — the
  // DB's earned_ge_spent check refuses the next spend. Surface the gap.
  const blocked = Math.max(0, c.balance - c.spendable_headroom);

  return (
    <div className="space-y-6">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Karma balance" value={karma(c.balance)} accent />
        <Stat
          label="Spendable headroom"
          value={karma(c.spendable_headroom)}
          hint={
            blocked > 0
              ? `${karma(blocked)} of the balance cannot be spent — lifetime earned minus spent is lower.`
              : 'min(balance, lifetime earned − spent). Matches the balance, as it should.'
          }
          tone={blocked > 0 ? 'warn' : undefined}
        />
        <Stat label="Lifetime earned" value={karma(c.total_earned)} />
        <Stat label="Lifetime spent" value={karma(c.total_spent)} />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Credits">
          <Row label="Earned today" value={`${karma(c.daily_earned)} karma`} />
          <Row label="Daily counter reset" value={absolute(c.daily_reset_at)} />
          <Row label="Locked in active escrow" value={`${karma(c.escrow_locked)} karma`} />
        </Panel>

        <Panel title="Score &amp; tier">
          <Row label="Score" value={user.score.score_updated_at ? num(Math.round(user.score.tweetscout_score)) : 'never scored'} />
          <Row label="Tier" value={user.score.tier} />
          <Row label="Karma multiplier" value={`${user.score.multiplier}×`} />
          <Row
            label="Last refreshed"
            value={user.score.score_updated_at ? absolute(user.score.score_updated_at) : '—'}
          />
        </Panel>

        <Panel title="Activity">
          <Row label="Engagements (counter / rows)" value={`${num(user.activity.total_engagements)} / ${num(user.activity.engagements_recorded)}`} />
          <Row label="Pending (unclaimed)" value={num(user.activity.engagements_pending)} />
          <Row label="Posts active / completed / cancelled" value={`${num(user.activity.posts_active)} / ${num(user.activity.posts_completed)} / ${num(user.activity.posts_cancelled)}`} />
          <Row label="Streak (current / longest)" value={`${num(user.activity.current_streak)} / ${num(user.activity.longest_streak)}`} />
          <Row label="Last engagement" value={user.activity.last_engagement_date ?? '—'} />
          <Row label="Honesty score" value={`${num(user.activity.honesty_score)} / 50`} />
        </Panel>

        <Panel title="Sponsored XP">
          <Row label="XP balance" value={num(user.xp.sponsored_xp)} />
          <Row label="Lifetime XP" value={num(user.xp.total_sponsored_xp_earned)} />
          <Row label="Sponsored engagements" value={num(user.xp.sponsored_engagements)} />
        </Panel>

        <Panel title="Identity">
          <Row label="Telegram" value={user.telegram_username ? `@${user.telegram_username}` : '—'} />
          <Row label="Display name" value={user.display_name || '—'} />
          <Row
            label="X handle"
            value={
              user.x_username ? (
                <a
                  href={`https://x.com/${user.x_username.replace(/^@/, '')}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-zinc-200 hover:text-[#f95400] hover:underline"
                >
                  @{user.x_username.replace(/^@/, '')}
                  <ExternalLink size={10} aria-hidden />
                </a>
              ) : '—'
            }
          />
          <Row label="X verified at" value={user.flags.x_verified_at ? absolute(user.flags.x_verified_at) : '—'} />
          {user.flags.pending_claimed_x_username && (
            <Row label="Pending claimed handle" value={`@${user.flags.pending_claimed_x_username}`} />
          )}
        </Panel>

        <Panel title="Waitlist &amp; referrals">
          {user.waitlist ? (
            <>
              <Row label="Entry status" value={user.waitlist.status} />
              <Row label="Applied" value={absolute(user.waitlist.created_at)} />
              <Row label="Approved" value={user.waitlist.approved_at ? absolute(user.waitlist.approved_at) : '—'} />
              <Row label="Region / niche" value={`${user.waitlist.region || '—'} / ${user.waitlist.niche || '—'}`} />
              {user.waitlist.rejection_reason && (
                <Row label="Rejection reason" value={user.waitlist.rejection_reason} />
              )}
            </>
          ) : (
            <p className="text-xs text-zinc-500">No waitlist entry (seeded or admin-created account).</p>
          )}
          <Row label="Referral code" value={user.referrals.code || '—'} />
          <Row label="Signed up with code" value={user.referrals.referred_by_code || '—'} />
          <Row label="Referrals made" value={num(user.referrals.referrals_made)} />
        </Panel>
      </div>
    </div>
  );
}

function Stat({
  label, value, hint, accent, tone,
}: {
  label: string; value: string; hint?: string; accent?: boolean; tone?: 'warn';
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-[#111] p-4">
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={cn(
        'mt-1 font-mono text-xl tabular-nums',
        tone === 'warn' ? 'text-amber-300' : accent ? 'text-[#f95400]' : 'text-white',
      )}>
        {value}
      </div>
      {hint && <p className={cn('mt-1.5 text-[11px]', tone === 'warn' ? 'text-amber-400/80' : 'text-zinc-600')}>{hint}</p>}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-white/[0.06] bg-[#111] p-4">
      <h2 className="font-syne text-sm font-semibold uppercase tracking-wide text-zinc-400">{title}</h2>
      <dl className="mt-3 space-y-1.5">{children}</dl>
    </section>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 text-sm">
      <dt className="text-zinc-500">{label}</dt>
      <dd className="text-right text-zinc-200">{value}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paged history tabs
// ---------------------------------------------------------------------------
function usePagedTab<T>(fetcher: (limit: number, offset: number) => Promise<{ rows: T[]; total: number }>, deps: React.DependencyList) {
  const [page, setPage] = useState(1);
  const resource = useAdminResource(
    () => fetcher(SUB_PAGE_SIZE, (page - 1) * SUB_PAGE_SIZE),
    [...deps, page],
  );
  return { ...resource, page, setPage };
}

function TabShell({
  loading, error, empty, emptyText, reload, children, page, total, setPage,
}: {
  loading: boolean;
  error: string | null;
  empty: boolean;
  emptyText: string;
  reload: () => void;
  children: React.ReactNode;
  page: number;
  total: number;
  setPage: (p: number) => void;
}) {
  if (loading && empty) return <TableSkeleton rows={5} />;
  if (error && empty) {
    return (
      <EmptyState
        icon={ShieldOff}
        title="Couldn't load"
        description={error}
        action={<Button variant="secondary" size="sm" onClick={reload}>Retry</Button>}
      />
    );
  }
  if (empty) return <EmptyState icon={ShieldOff} title="Nothing here yet" description={emptyText} />;
  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-2xl border border-white/[0.06]">{children}</div>
      <Pagination page={page} pageSize={SUB_PAGE_SIZE} total={total} onPageChange={setPage} />
    </div>
  );
}

const TX_TONE: Record<string, string> = {
  earned: 'text-emerald-400',
  refund: 'text-emerald-400',
  admin_grant: 'text-emerald-400',
  spent: 'text-zinc-400',
  apply_penalty: 'text-red-400',
};

function LedgerTab({ userId }: { userId: string }) {
  const { data, loading, error, reload, page, setPage } = usePagedTab<AdminUserTransaction>(
    (limit, offset) => adminApi.getUserTransactions(userId, limit, offset), [userId],
  );
  const rows = data?.rows ?? [];
  return (
    <TabShell
      loading={loading} error={error} empty={rows.length === 0}
      emptyText="No karma has moved on this account." reload={reload}
      page={page} total={data?.total ?? 0} setPage={setPage}
    >
      <TableScroll minWidth={760}>
        <table className="w-full min-w-full text-sm">
          <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
            <tr>
              <th scope="col" className="px-4 py-3 font-semibold">When</th>
              <th scope="col" className="px-4 py-3 font-semibold">Type</th>
              <th scope="col" className="px-4 py-3 text-right font-semibold">Amount</th>
              <th scope="col" className="px-4 py-3 text-right font-semibold">Balance after</th>
              <th scope="col" className="px-4 py-3 font-semibold">Description</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {rows.map((t) => (
              <tr key={t.id} className="bg-[#111]">
                <td className="px-4 py-3 text-zinc-400" title={t.created_at ?? ''}>{timeAgo(t.created_at)}</td>
                <td className="px-4 py-3"><Badge tone="neutral">{t.type.replace('_', ' ')}</Badge></td>
                <td className={cn('px-4 py-3 text-right font-mono tabular-nums', TX_TONE[t.type] ?? 'text-zinc-200')}>
                  {t.amount > 0 ? '+' : ''}{karma(t.amount)}
                </td>
                <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-400">{karma(t.balance_after)}</td>
                <td className="max-w-[320px] truncate px-4 py-3 text-zinc-400" title={t.description}>{t.description || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </TabShell>
  );
}

function PostsTab({ userId }: { userId: string }) {
  const { data, loading, error, reload, page, setPage } = usePagedTab<AdminUserPost>(
    (limit, offset) => adminApi.getUserPosts(userId, limit, offset), [userId],
  );
  const rows = data?.rows ?? [];
  return (
    <TabShell
      loading={loading} error={error} empty={rows.length === 0}
      emptyText="This user has never submitted a post." reload={reload}
      page={page} total={data?.total ?? 0} setPage={setPage}
    >
      <TableScroll minWidth={820}>
        <table className="w-full min-w-full text-sm">
          <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
            <tr>
              <th scope="col" className="px-4 py-3 font-semibold">When</th>
              <th scope="col" className="px-4 py-3 font-semibold">Post</th>
              <th scope="col" className="px-4 py-3 font-semibold">Status</th>
              <th scope="col" className="px-4 py-3 text-right font-semibold">Escrow left / initial</th>
              <th scope="col" className="px-4 py-3 text-right font-semibold">Engagements</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {rows.map((p) => (
              <tr key={p.id} className="bg-[#111]">
                <td className="px-4 py-3 text-zinc-400" title={p.created_at ?? ''}>{timeAgo(p.created_at)}</td>
                <td className="max-w-[300px] px-4 py-3">
                  <a
                    href={p.x_link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-zinc-300 hover:text-[#f95400] hover:underline"
                  >
                    <span className="truncate">{p.tweet_text || p.x_link}</span>
                    <ExternalLink size={10} className="shrink-0" aria-hidden />
                  </a>
                </td>
                <td className="px-4 py-3">
                  <Badge tone={p.status === 'active' ? 'success' : p.status === 'cancelled' ? 'danger' : 'neutral'}>
                    {p.status}
                  </Badge>
                  {p.is_sponsored && <Badge tone="info" className="ml-1">sponsored</Badge>}
                </td>
                <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-300">
                  {karma(p.escrow)} / {karma(p.initial_escrow)}
                </td>
                <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-400">{num(p.engagements)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </TabShell>
  );
}

function EngagementsTab({ userId }: { userId: string }) {
  const { data, loading, error, reload, page, setPage } = usePagedTab<AdminUserEngagement>(
    (limit, offset) => adminApi.getUserEngagements(userId, limit, offset), [userId],
  );
  const rows = data?.rows ?? [];
  return (
    <TabShell
      loading={loading} error={error} empty={rows.length === 0}
      emptyText="This user has never engaged with a post." reload={reload}
      page={page} total={data?.total ?? 0} setPage={setPage}
    >
      <TableScroll minWidth={780}>
        <table className="w-full min-w-full text-sm">
          <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
            <tr>
              <th scope="col" className="px-4 py-3 font-semibold">Clicked</th>
              <th scope="col" className="px-4 py-3 font-semibold">Post</th>
              <th scope="col" className="px-4 py-3 font-semibold">Verified</th>
              <th scope="col" className="px-4 py-3 font-semibold">Credited</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {rows.map((e) => (
              <tr key={e.id} className="bg-[#111]">
                <td className="px-4 py-3 text-zinc-400" title={e.clicked_at ?? ''}>{timeAgo(e.clicked_at)}</td>
                <td className="max-w-[300px] px-4 py-3">
                  <a
                    href={e.post_link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-zinc-300 hover:text-[#f95400] hover:underline"
                  >
                    <span className="truncate">{e.post_author ? `@${e.post_author}` : e.post_link}</span>
                    <ExternalLink size={10} className="shrink-0" aria-hidden />
                  </a>
                  {e.is_sponsored && <Badge tone="info" className="ml-1">sponsored</Badge>}
                </td>
                <td className="px-4 py-3">
                  {e.verified
                    ? <Badge tone="success">verified</Badge>
                    : <Badge tone="warning">pending</Badge>}
                  {e.like_verified && <Badge tone="neutral" className="ml-1">like</Badge>}
                  {e.reply_verified && <Badge tone="neutral" className="ml-1">reply</Badge>}
                </td>
                <td className="px-4 py-3 text-zinc-400">{e.credit_granted ? 'yes' : 'no'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </TabShell>
  );
}

function AuditTab({ userId }: { userId: string }) {
  const { data, loading, error, reload, page, setPage } = usePagedTab<AdminUserAuditRow>(
    (limit, offset) => adminApi.getUserAudit(userId, limit, offset), [userId],
  );
  const rows = data?.rows ?? [];
  return (
    <TabShell
      loading={loading} error={error} empty={rows.length === 0}
      emptyText="No admin has ever acted on this account." reload={reload}
      page={page} total={data?.total ?? 0} setPage={setPage}
    >
      <TableScroll minWidth={760}>
        <table className="w-full min-w-full text-sm">
          <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
            <tr>
              <th scope="col" className="px-4 py-3 font-semibold">When</th>
              <th scope="col" className="px-4 py-3 font-semibold">Action</th>
              <th scope="col" className="px-4 py-3 font-semibold">By</th>
              <th scope="col" className="px-4 py-3 font-semibold">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {rows.map((a) => (
              <tr key={a.id} className="bg-[#111]">
                <td className="px-4 py-3 text-zinc-400" title={absolute(a.created_at, { seconds: true })}>
                  {timeAgo(a.created_at)}
                </td>
                <td className="px-4 py-3"><Badge tone="neutral">{a.action.replace(/_/g, ' ')}</Badge></td>
                <td className="px-4 py-3 text-zinc-300">
                  {a.actor_handle ? `@${a.actor_handle}` : (a.actor_id ? `${a.actor_id.slice(0, 8)}…` : 'system')}
                  {a.actor_role && <span className="ml-1 text-[11px] text-zinc-600">{a.actor_role}</span>}
                </td>
                <td className="px-4 py-3">
                  <AuditDetail detail={a.detail} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </TabShell>
  );
}

/** Render the JSONB detail as key: value pairs; skip the empty/noise fields. */
function AuditDetail({ detail }: { detail: Record<string, unknown> }) {
  const entries = useMemo(
    () => Object.entries(detail ?? {}).filter(([, v]) => v !== '' && v !== null && v !== false),
    [detail],
  );
  if (entries.length === 0) return <span className="text-zinc-600">—</span>;
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
      {entries.map(([k, v]) => (
        <span key={k} className="text-zinc-400">
          <span className="text-zinc-600">{k.replace(/_/g, ' ')}:</span>{' '}
          <span className="font-mono text-zinc-300">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}
