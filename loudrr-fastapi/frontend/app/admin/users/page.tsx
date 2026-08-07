'use client';

import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  Search,
  Plus,
  Minus,
  Ban,
  UserCheck,
  RefreshCcw,
  Users as UsersIcon,
  CheckCircle2,
  Crown,
  Shield,
} from 'lucide-react';
import { adminApi, AdminUserRow } from '@/lib/api';
import { Button } from '@/components/admin/Button';
import { Modal } from '@/components/admin/Modal';
import { Input, Textarea } from '@/components/admin/Input';
import { Badge } from '@/components/admin/Badge';
import { EmptyState } from '@/components/admin/EmptyState';
import { TableSkeleton } from '@/components/admin/Skeleton';
import { cn } from '@/lib/utils';

type ActionKind = 'grant' | 'revoke' | 'ban' | 'unban' | null;

interface ActionState {
  kind: ActionKind;
  user: AdminUserRow | null;
  amount: string;
  reason: string;
}

const EMPTY_ACTION: ActionState = { kind: null, user: null, amount: '', reason: '' };

export default function AdminUsersPage() {
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<AdminUserRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [action, setAction] = useState<ActionState>(EMPTY_ACTION);

  async function load(q: string) {
    setError(null);
    try {
      setRows(await adminApi.searchUsers(q, 100));
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      toast.error('Failed to load users', { description: msg });
    }
  }

  useEffect(() => { load(''); }, []);

  function startAction(kind: NonNullable<ActionKind>, user: AdminUserRow) {
    setAction({ kind, user, amount: '', reason: '' });
  }

  function closeAction() {
    setAction(EMPTY_ACTION);
  }

  async function performAction() {
    if (!action.kind || !action.user) return;
    const u = action.user;
    setBusyId(u.id);
    try {
      if (action.kind === 'grant') {
        const amount = parseFloat(action.amount);
        if (!isFinite(amount) || amount <= 0) {
          toast.error('Invalid amount', { description: 'Must be a positive number.' });
          setBusyId(null);
          return;
        }
        await adminApi.grantCredits(u.id, amount, action.reason);
        toast.success(`Granted ${amount} credits`, {
          description: `→ @${u.telegram_username || u.x_username || u.id.slice(0, 8)}`,
        });
      } else if (action.kind === 'revoke') {
        const amount = parseFloat(action.amount);
        if (!isFinite(amount) || amount <= 0) {
          toast.error('Invalid amount', { description: 'Must be a positive number.' });
          setBusyId(null);
          return;
        }
        await adminApi.revokeCredits(u.id, amount, action.reason);
        toast.success(`Revoked ${amount} credits`, {
          description: `→ @${u.telegram_username || u.x_username || u.id.slice(0, 8)}`,
        });
      } else if (action.kind === 'ban') {
        await adminApi.banUser(u.id, action.reason);
        toast.success('User banned', {
          description: `@${u.telegram_username || u.x_username || u.id.slice(0, 8)}${action.reason ? ` — ${action.reason}` : ''}`,
        });
      } else if (action.kind === 'unban') {
        await adminApi.unbanUser(u.id);
        toast.success('User unbanned', {
          description: `@${u.telegram_username || u.x_username || u.id.slice(0, 8)}`,
        });
      }
      closeAction();
      await load(query);
    } catch (e) {
      const msg = (e as Error).message;
      toast.error(`${action.kind} failed`, { description: msg });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-syne text-2xl font-bold tracking-tight">Users</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {rows === null ? 'Loading…' : `${rows.length}${rows.length === 100 ? '+' : ''} user${rows.length === 1 ? '' : 's'}${query ? ` matching "${query}"` : ' (recent first)'}`}
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => load(query)} disabled={rows === null}>
          <RefreshCcw size={12} />
          Refresh
        </Button>
      </div>

      <form
        className="flex items-center gap-2"
        onSubmit={(e) => { e.preventDefault(); load(query); }}
      >
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search telegram_username or x_username (case-insensitive substring)…"
            className="w-full rounded-lg border border-white/[0.08] bg-[#0a0a0a] py-2 pl-9 pr-3 text-sm text-white placeholder:text-zinc-600 focus:border-[#f95400]/40 focus:outline-none focus:ring-1 focus:ring-[#f95400]/40"
          />
        </div>
        <Button type="submit" variant="primary">Search</Button>
        {query && (
          <Button type="button" variant="ghost" onClick={() => { setQuery(''); load(''); }}>Clear</Button>
        )}
      </form>

      {error && <div className="rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">{error}</div>}

      {rows === null ? (
        <TableSkeleton rows={5} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title="No users match"
          description={query ? `Nothing found for "${query}". Try a partial handle.` : 'No users in the database yet.'}
        />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-white/[0.06]">
          <table className="w-full text-sm">
            <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-4 py-3 font-semibold">User</th>
                <th className="px-4 py-3 font-semibold">X handle</th>
                <th className="px-4 py-3 font-semibold text-right">Credits</th>
                <th className="px-4 py-3 font-semibold">Role</th>
                <th className="px-4 py-3 font-semibold">Flags</th>
                <th className="px-4 py-3 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {rows.map((u) => (
                <tr key={u.id} className="bg-[#111] hover:bg-[#161616] transition-colors">
                  <td className="px-4 py-3">
                    {u.telegram_username ? (
                      <div>
                        <div className="text-white">@{u.telegram_username}</div>
                        <div className="font-mono text-[11px] text-zinc-600">{u.telegram_id ?? ''}</div>
                      </div>
                    ) : (
                      <span className="text-zinc-600 font-mono text-xs">{u.id.slice(0, 8)}…</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {u.x_username ? (
                      <a
                        href={`https://x.com/${u.x_username.replace(/^@/, '')}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-zinc-300 hover:text-[#f95400] hover:underline"
                      >
                        @{u.x_username.replace(/^@/, '')}
                      </a>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                    {u.credits.toFixed(2)}
                  </td>
                  <td className="px-4 py-3">
                    {u.role === 'superadmin' ? (
                      <Badge tone="superadmin"><Crown size={9} />superadmin</Badge>
                    ) : u.role === 'admin' ? (
                      <Badge tone="admin"><Shield size={9} />admin</Badge>
                    ) : (
                      <span className="text-zinc-700 text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {u.is_banned && <Badge tone="danger">banned</Badge>}
                      {u.is_whitelisted && <Badge tone="warning">whitelisted</Badge>}
                      {u.x_verified && <Badge tone="success"><CheckCircle2 size={9} />x-verified</Badge>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="inline-flex gap-1">
                      <Button size="sm" variant="success" disabled={busyId === u.id} onClick={() => startAction('grant', u)} title="Grant credits">
                        <Plus size={11} />
                      </Button>
                      <Button size="sm" variant="secondary" disabled={busyId === u.id} onClick={() => startAction('revoke', u)} title="Revoke credits (superadmin)">
                        <Minus size={11} />
                      </Button>
                      {u.is_banned ? (
                        <Button size="sm" variant="secondary" disabled={busyId === u.id} onClick={() => startAction('unban', u)} title="Unban">
                          <UserCheck size={11} />
                        </Button>
                      ) : (
                        <Button size="sm" variant="danger" disabled={busyId === u.id} onClick={() => startAction('ban', u)} title="Ban">
                          <Ban size={11} />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Action modal — single modal handles grant/revoke/ban/unban */}
      <Modal
        open={action.kind !== null}
        onClose={closeAction}
        title={modalTitle(action)}
        description={modalDescription(action)}
        footer={
          <>
            <Button variant="secondary" onClick={closeAction}>Cancel</Button>
            <Button
              variant={action.kind === 'grant' || action.kind === 'unban' ? 'success' : 'danger'}
              loading={busyId === action.user?.id}
              onClick={performAction}
            >
              Confirm {action.kind}
            </Button>
          </>
        }
      >
        {action.user && (
          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
              <div className="grid grid-cols-[100px_1fr] gap-y-1.5">
                <span className="text-zinc-500">User</span>
                <span className="text-zinc-200">@{action.user.telegram_username || action.user.x_username || action.user.id.slice(0, 8)}</span>
                <span className="text-zinc-500">Current credits</span>
                <span className="font-mono text-zinc-200">{action.user.credits.toFixed(2)}</span>
                <span className="text-zinc-500">Role</span>
                <span className="text-zinc-200">{action.user.role || 'user'}</span>
                <span className="text-zinc-500">Banned</span>
                <span className={cn(action.user.is_banned ? 'text-red-400' : 'text-zinc-200')}>{action.user.is_banned ? 'yes' : 'no'}</span>
              </div>
            </div>

            {(action.kind === 'grant' || action.kind === 'revoke') && (
              <Input
                label="Amount"
                type="number"
                step="0.01"
                min="0.01"
                placeholder="e.g. 25"
                value={action.amount}
                onChange={(e) => setAction({ ...action, amount: e.target.value })}
                hint={action.kind === 'revoke' ? 'Requires superadmin role. Will clamp to current balance.' : undefined}
              />
            )}

            {action.kind !== 'unban' && (
              <Textarea
                label={action.kind === 'ban' ? 'Reason' : 'Description (audit log)'}
                placeholder={action.kind === 'ban' ? 'e.g. botting, fraud, ToS violation…' : 'e.g. promo bonus, partnership reward…'}
                value={action.reason}
                onChange={(e) => setAction({ ...action, reason: e.target.value })}
                rows={2}
              />
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

function modalTitle(a: ActionState): string {
  if (!a.user) return '';
  const handle = a.user.telegram_username || a.user.x_username || a.user.id.slice(0, 8);
  switch (a.kind) {
    case 'grant': return `Grant credits to @${handle}`;
    case 'revoke': return `Revoke credits from @${handle}`;
    case 'ban': return `Ban @${handle}?`;
    case 'unban': return `Unban @${handle}?`;
    default: return '';
  }
}

function modalDescription(a: ActionState): string {
  switch (a.kind) {
    case 'grant': return 'Audit-logged. Increments credits + total_credits_earned + writes an admin_grant transaction.';
    case 'revoke': return 'Audit-logged. Calls apply_penalty which clamps to current balance (never goes negative).';
    case 'ban': return 'Audit-logged. Sets is_banned=true and clears is_whitelisted to satisfy the DB constraint.';
    case 'unban': return 'Audit-logged. Sets is_banned=false. Whitelist status is NOT restored automatically.';
    default: return '';
  }
}
