'use client';

/**
 * Users — search, filter, page, and act.
 *
 * The search, the flag filter, the sort and the paging are all SERVER-side
 * (`GET /api/admin/users/` returns `{rows, total}`), so the count in the
 * header is the real number of matches rather than "however many the
 * hardcoded limit fetched".
 */

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ArrowDown,
  ArrowUp,
  Ban,
  CheckCircle2,
  Crown,
  Minus,
  Plus,
  RefreshCcw,
  Search,
  Shield,
  UserCheck,
  Users as UsersIcon,
  X,
} from 'lucide-react';

import {
  adminApi,
  type AdminUserFlag,
  type AdminUserRow,
  type AdminUserSort,
} from '@/lib/api';
import { Badge } from '@/components/admin/Badge';
import { Button } from '@/components/admin/Button';
import { CopyButton } from '@/components/admin/CopyButton';
import { EmptyState } from '@/components/admin/EmptyState';
import { Pagination } from '@/components/admin/Pagination';
import { TableScroll } from '@/components/admin/TableScroll';
import { TableSkeleton } from '@/components/admin/Skeleton';
import { RequiresRole, useAdminSession } from '@/lib/admin/session';
import { useAdminResource } from '@/lib/admin/useAdminResource';
import { karma, num, timeAgo } from '@/lib/admin/format';
import { cn } from '@/lib/utils';
import {
  identityLabel,
  newRequestId,
  UserActionDialog,
  type ActionTarget,
  type UserAction,
} from './UserActionDialog';

const PAGE_SIZE = 25;

const FLAGS: Array<{ value: AdminUserFlag; label: string }> = [
  { value: '', label: 'All' },
  { value: 'banned', label: 'Banned' },
  { value: 'not_whitelisted', label: 'Not whitelisted' },
  { value: 'admins', label: 'Admins' },
  { value: 'never_scored', label: 'Never scored' },
];

const SORTS: Array<{ value: AdminUserSort; label: string }> = [
  { value: 'created_at', label: 'Joined' },
  { value: 'credits', label: 'Karma' },
  { value: 'tweetscout_score', label: 'Score' },
  { value: 'total_engagements', label: 'Engagements' },
];

export default function AdminUsersPage() {
  const router = useRouter();
  const { me } = useAdminSession();

  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [flag, setFlag] = useState<AdminUserFlag>('');
  const [sort, setSort] = useState<AdminUserSort>('created_at');
  const [dir, setDir] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  // `requestId` is minted here, in the click handler, so the grant/revoke it
  // performs is idempotent across a double-click or a retried fetch.
  const [action, setAction] =
    useState<{ kind: UserAction; user: AdminUserRow; requestId: string } | null>(null);
  const open = (kind: UserAction, user: AdminUserRow) =>
    setAction({ kind, user, requestId: newRequestId() });

  // Debounce the box so typing doesn't fire a query per keystroke, but keep
  // Enter working for the impatient. Every filter change also resets to page 1
  // — a narrower result set must not leave you parked on an empty page 4.
  useEffect(() => {
    const t = setTimeout(() => { setQuery(input.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [input]);

  function applySearch() { setQuery(input.trim()); setPage(1); }
  function applyFlag(next: AdminUserFlag) { setFlag(next); setPage(1); }
  function applySort(next: AdminUserSort) { setSort(next); setPage(1); }
  function applyDir(next: 'asc' | 'desc') { setDir(next); setPage(1); }

  const { data, loading, error, reload } = useAdminResource(
    () => adminApi.listUsers({
      q: query, flag, sort, dir, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE,
    }),
    [query, flag, sort, dir, page],
  );

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;

  const onDone = useCallback(async () => { await reload(); }, [reload]);

  function toggleSort(next: AdminUserSort) {
    if (next === sort) applyDir(dir === 'desc' ? 'asc' : 'desc');
    else { setSort(next); setDir('desc'); setPage(1); }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-syne text-2xl font-bold tracking-tight">Users</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {loading && !data
              ? 'Loading…'
              : `${num(total)} user${total === 1 ? '' : 's'}${query ? ` matching “${query}”` : ''}${flag ? ` · ${FLAGS.find((f) => f.value === flag)?.label.toLowerCase()}` : ''}`}
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
          <RefreshCcw size={12} aria-hidden />
          Refresh
        </Button>
      </header>

      {/* ---- search + filters ---- */}
      <div className="space-y-3">
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => { e.preventDefault(); applySearch(); }}
          role="search"
        >
          <div className="relative flex-1">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600" aria-hidden />
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              aria-label="Search users"
              placeholder="Handle, display name, referral code, telegram id or user id…"
              className="w-full rounded-lg border border-white/[0.08] bg-[#0a0a0a] py-2 pl-9 pr-9 text-sm text-white placeholder:text-zinc-600 focus:border-[#f95400]/40 focus:outline-none focus:ring-1 focus:ring-[#f95400]/40"
            />
            {input && (
              <button
                type="button"
                onClick={() => setInput('')}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
              >
                <X size={13} aria-hidden />
              </button>
            )}
          </div>
        </form>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter users">
            {FLAGS.map((f) => (
              <button
                key={f.value || 'all'}
                type="button"
                onClick={() => applyFlag(f.value)}
                aria-pressed={flag === f.value}
                className={cn(
                  'rounded-full border px-3 py-1 text-xs transition-colors',
                  flag === f.value
                    ? 'border-[#f95400]/40 bg-[#f95400]/15 text-[#ff9d6b]'
                    : 'border-white/[0.08] text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200',
                )}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-1.5">
            <label htmlFor="user-sort" className="text-xs text-zinc-500">Sort</label>
            <select
              id="user-sort"
              value={sort}
              onChange={(e) => applySort(e.target.value as AdminUserSort)}
              className="rounded-lg border border-white/[0.08] bg-[#0a0a0a] px-2 py-1.5 text-xs text-zinc-200 focus:border-[#f95400]/40 focus:outline-none"
            >
              {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <Button
              variant="secondary"
              size="sm"
              aria-label={dir === 'desc' ? 'Sort ascending' : 'Sort descending'}
              onClick={() => applyDir(dir === 'desc' ? 'asc' : 'desc')}
            >
              {dir === 'desc' ? <ArrowDown size={12} aria-hidden /> : <ArrowUp size={12} aria-hidden />}
              {dir === 'desc' ? 'Desc' : 'Asc'}
            </Button>
          </div>
        </div>
      </div>

      {error && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">
          <span>{error}</span>
          <Button variant="secondary" size="sm" onClick={reload}>Retry</Button>
        </div>
      )}

      {/* ---- table ---- */}
      {loading && !data ? (
        <TableSkeleton rows={6} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title="No users match"
          description={
            query || flag
              ? 'Nothing matches these filters. Try a partial handle, a telegram id, or clear the filter.'
              : 'No users in the database yet.'
          }
          action={
            (query || flag) ? (
              <Button variant="secondary" size="sm" onClick={() => { setInput(''); applyFlag(''); }}>
                Clear filters
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <div className="overflow-hidden rounded-2xl border border-white/[0.06]">
            <TableScroll minWidth={1080}>
              <table className="w-full min-w-full text-sm">
                <caption className="sr-only">
                  Users. Select a row to open the full account detail.
                </caption>
                <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
                  <tr>
                    <th scope="col" className="px-4 py-3 font-semibold">User</th>
                    <th scope="col" className="px-4 py-3 font-semibold">X handle</th>
                    <SortableHeader label="Karma" value="credits" sort={sort} dir={dir} onSort={toggleSort} align="right" />
                    <SortableHeader label="Score" value="tweetscout_score" sort={sort} dir={dir} onSort={toggleSort} align="right" />
                    <SortableHeader label="Engagements" value="total_engagements" sort={sort} dir={dir} onSort={toggleSort} align="right" />
                    <SortableHeader label="Joined" value="created_at" sort={sort} dir={dir} onSort={toggleSort} />
                    <th scope="col" className="px-4 py-3 font-semibold">Role &amp; flags</th>
                    <th scope="col" className="px-4 py-3 text-right font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04]">
                  {rows.map((u) => {
                    const isSelf = me?.id === u.id;
                    return (
                      <tr
                        key={u.id}
                        onClick={() => router.push(`/admin/users/${u.id}`)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            router.push(`/admin/users/${u.id}`);
                          }
                        }}
                        tabIndex={0}
                        role="link"
                        aria-label={`Open ${identityLabel(u)}`}
                        className="cursor-pointer bg-[#111] transition-colors hover:bg-[#161616] focus:bg-[#161616] focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[#f95400]/50"
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-start gap-2">
                            <div className="min-w-0">
                              <div className="truncate text-white">
                                {identityLabel(u)}
                                {isSelf && <span className="ml-1.5 text-[10px] uppercase text-[#f95400]">you</span>}
                              </div>
                              {/* the telegram id is ALWAYS shown — it is the
                                  only stable handle for a user with no @name */}
                              <div className="font-mono text-[11px] text-zinc-600">
                                tg {u.telegram_id ?? '—'}
                              </div>
                            </div>
                            {/* short visible label keeps the cell narrow on a
                                phone; the button's own aria-label reads
                                "Copy id" */}
                            <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                              <CopyButton value={u.id} label="id" />
                            </span>
                          </div>
                        </td>

                        <td className="px-4 py-3">
                          {u.x_username ? (
                            <a
                              href={`https://x.com/${u.x_username.replace(/^@/, '')}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-zinc-300 hover:text-[#f95400] hover:underline"
                            >
                              @{u.x_username.replace(/^@/, '')}
                            </a>
                          ) : (
                            <span className="text-zinc-600">—</span>
                          )}
                        </td>

                        <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                          {karma(u.credits)}
                        </td>

                        <td className="px-4 py-3 text-right">
                          {u.score_updated_at ? (
                            <>
                              <div className="font-mono tabular-nums text-zinc-200">
                                {num(Math.round(u.tweetscout_score))}
                              </div>
                              <div className="text-[11px] text-zinc-500">{u.tier}</div>
                            </>
                          ) : (
                            <span className="text-zinc-600" title="Never scored">—</span>
                          )}
                        </td>

                        <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-400">
                          {num(u.total_engagements)}
                        </td>

                        <td className="px-4 py-3 text-zinc-400" title={u.created_at ?? ''}>
                          {timeAgo(u.created_at)}
                        </td>

                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-1">
                            {u.role === 'superadmin' && <Badge tone="superadmin"><Crown size={9} aria-hidden />superadmin</Badge>}
                            {u.role === 'admin' && <Badge tone="admin"><Shield size={9} aria-hidden />admin</Badge>}
                            {u.is_banned && <Badge tone="danger">banned</Badge>}
                            {u.is_whitelisted && <Badge tone="warning">whitelisted</Badge>}
                            {u.x_verified && <Badge tone="success"><CheckCircle2 size={9} aria-hidden />x-verified</Badge>}
                            {!u.role && !u.is_banned && !u.is_whitelisted && !u.x_verified && (
                              <span className="text-xs text-zinc-700">—</span>
                            )}
                          </div>
                        </td>

                        <td className="px-4 py-3">
                          <div
                            className="flex items-center justify-end gap-2"
                            onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => e.stopPropagation()}
                          >
                            <Button
                              size="sm"
                              variant="success"
                              aria-label={`Grant karma to ${identityLabel(u)}`}
                              onClick={() => open('grant', u)}
                            >
                              <Plus size={11} aria-hidden />
                              Grant
                            </Button>

                            {/* superadmin-only on the server — say so BEFORE
                                an admin types an amount and a reason */}
                            <RequiresRole role="superadmin">
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={isSelf}
                                aria-label={`Revoke karma from ${identityLabel(u)}`}
                                onClick={() => open('revoke', u)}
                              >
                                <Minus size={11} aria-hidden />
                                Revoke
                              </Button>
                            </RequiresRole>

                            {u.is_banned ? (
                              <Button
                                size="sm"
                                variant="secondary"
                                aria-label={`Unban ${identityLabel(u)}`}
                                onClick={() => open('unban', u)}
                              >
                                <UserCheck size={11} aria-hidden />
                                Unban
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                variant="danger"
                                disabled={isSelf}
                                title={isSelf ? 'You cannot ban your own account' : undefined}
                                aria-label={`Ban ${identityLabel(u)}`}
                                onClick={() => open('ban', u)}
                              >
                                <Ban size={11} aria-hidden />
                                Ban
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          </div>

          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}

      {/* keyed per opening: the dialog remounts with empty fields instead of
          resetting them from an effect */}
      <UserActionDialog
        key={action ? `${action.kind}-${action.user.id}-${action.requestId}` : 'idle'}
        action={action?.kind ?? null}
        target={action ? toTarget(action.user) : null}
        requestId={action?.requestId ?? ''}
        onClose={() => setAction(null)}
        onDone={onDone}
      />
    </div>
  );
}

function toTarget(u: AdminUserRow): ActionTarget {
  return {
    id: u.id,
    telegram_id: u.telegram_id,
    telegram_username: u.telegram_username,
    x_username: u.x_username,
    display_name: u.display_name,
    credits: u.credits,
    role: u.role,
    is_banned: u.is_banned,
    is_whitelisted: u.is_whitelisted,
  };
}

function SortableHeader({
  label, value, sort, dir, onSort, align,
}: {
  label: string;
  value: AdminUserSort;
  sort: AdminUserSort;
  dir: 'asc' | 'desc';
  onSort: (v: AdminUserSort) => void;
  align?: 'right';
}) {
  const active = sort === value;
  return (
    <th
      scope="col"
      aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={cn('px-4 py-3 font-semibold', align === 'right' && 'text-right')}
    >
      <button
        type="button"
        onClick={() => onSort(value)}
        className={cn(
          'inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-zinc-300',
          active ? 'text-[#f95400]' : 'text-zinc-500',
        )}
      >
        {label}
        {active && (dir === 'asc'
          ? <ArrowUp size={10} aria-hidden />
          : <ArrowDown size={10} aria-hidden />)}
      </button>
    </th>
  );
}
