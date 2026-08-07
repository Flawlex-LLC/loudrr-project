'use client';

import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Check, X, RefreshCcw, UserCheck } from 'lucide-react';
import { adminApi, PendingWaitlistEntry } from '@/lib/api';
import { Button } from '@/components/admin/Button';
import { Modal } from '@/components/admin/Modal';
import { Textarea } from '@/components/admin/Input';
import { EmptyState } from '@/components/admin/EmptyState';
import { Badge } from '@/components/admin/Badge';
import { TableSkeleton } from '@/components/admin/Skeleton';

export default function AdminWaitlistPage() {
  const [entries, setEntries] = useState<PendingWaitlistEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rejectTarget, setRejectTarget] = useState<PendingWaitlistEntry | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  async function load() {
    setError(null);
    try {
      setEntries(await adminApi.pendingWaitlist(200));
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      toast.error('Failed to load waitlist', { description: msg });
    }
  }

  useEffect(() => { load(); }, []);

  async function approve(entry: PendingWaitlistEntry) {
    setBusyId(entry.id);
    try {
      await adminApi.approveWaitlist(entry.id);
      toast.success('Approved', {
        description: `@${entry.telegram_username || entry.x_username || entry.id} is now a user.`,
      });
      await load();
    } catch (e) {
      toast.error('Approve failed', { description: (e as Error).message });
    } finally {
      setBusyId(null);
    }
  }

  async function confirmReject() {
    if (!rejectTarget) return;
    setBusyId(rejectTarget.id);
    try {
      await adminApi.rejectWaitlist(rejectTarget.id, rejectReason);
      toast.success('Rejected', {
        description: `Entry for @${rejectTarget.telegram_username || rejectTarget.x_username || rejectTarget.id} rejected${rejectReason ? ` (${rejectReason})` : ''}.`,
      });
      setRejectTarget(null);
      setRejectReason('');
      await load();
    } catch (e) {
      toast.error('Reject failed', { description: (e as Error).message });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-syne text-2xl font-bold tracking-tight">Pending Waitlist</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {entries === null ? 'Loading…' : entries.length === 0 ? 'Inbox zero. Nothing pending.' : `${entries.length} entr${entries.length === 1 ? 'y' : 'ies'} awaiting review (oldest first)`}
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={load} disabled={entries === null}>
          <RefreshCcw size={12} />
          Refresh
        </Button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">{error}</div>
      )}

      {entries === null ? (
        <TableSkeleton rows={5} />
      ) : entries.length === 0 ? (
        <EmptyState icon={UserCheck} title="No pending entries" description="New waitlist signups will appear here for approval." />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-white/[0.06]">
          <table className="w-full text-sm">
            <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-4 py-3 font-semibold">Telegram</th>
                <th className="px-4 py-3 font-semibold">X handle</th>
                <th className="px-4 py-3 font-semibold">Region / Niche</th>
                <th className="px-4 py-3 font-semibold">Submitted</th>
                <th className="px-4 py-3 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {entries.map((e) => (
                <tr key={e.id} className="bg-[#111] hover:bg-[#161616] transition-colors">
                  <td className="px-4 py-3">
                    {e.telegram_username ? (
                      <div>
                        <div className="text-white">@{e.telegram_username}</div>
                        <div className="text-[11px] text-zinc-600 font-mono">{e.telegram_id ?? ''}</div>
                      </div>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {e.x_username ? (
                      <a
                        href={`https://x.com/${e.x_username.replace(/^@/, '')}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-white hover:text-[#f95400] hover:underline"
                      >
                        @{e.x_username.replace(/^@/, '')}
                      </a>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-400">
                    {e.region || <span className="text-zinc-700">—</span>}
                    <span className="px-1 text-zinc-700">/</span>
                    {e.niche || <span className="text-zinc-700">—</span>}
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-500">
                    {e.created_at ? new Date(e.created_at).toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="inline-flex gap-1.5">
                      <Button
                        size="sm"
                        variant="success"
                        loading={busyId === e.id}
                        onClick={() => approve(e)}
                      >
                        <Check size={12} />
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={busyId === e.id}
                        onClick={() => { setRejectTarget(e); setRejectReason(''); }}
                      >
                        <X size={12} />
                        Reject
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={!!rejectTarget}
        onClose={() => { setRejectTarget(null); setRejectReason(''); }}
        title={`Reject @${rejectTarget?.telegram_username || rejectTarget?.x_username || 'entry'}?`}
        description="This will mark the waitlist entry rejected and audit-log the action. The applicant won't be re-prompted automatically."
        footer={
          <>
            <Button variant="secondary" onClick={() => setRejectTarget(null)}>Cancel</Button>
            <Button
              variant="danger"
              loading={busyId === rejectTarget?.id}
              onClick={confirmReject}
            >
              Confirm Reject
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
            <div className="grid grid-cols-2 gap-y-1.5 gap-x-4">
              <span className="text-zinc-500">Telegram</span><span className="font-mono text-zinc-200">{rejectTarget?.telegram_username ? `@${rejectTarget.telegram_username}` : `#${rejectTarget?.telegram_id ?? '—'}`}</span>
              <span className="text-zinc-500">X handle</span><span className="text-zinc-200">{rejectTarget?.x_username ? `@${rejectTarget.x_username.replace(/^@/, '')}` : '—'}</span>
              <span className="text-zinc-500">Region</span><span className="text-zinc-200">{rejectTarget?.region || '—'}</span>
              <span className="text-zinc-500">Niche</span><span className="text-zinc-200">{rejectTarget?.niche || '—'}</span>
            </div>
          </div>
          <Textarea
            label="Rejection reason (optional, internal)"
            placeholder="e.g. bot, profile doesn't match niche, low signal account…"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            rows={3}
            hint="Visible only in audit logs; the applicant doesn't see it."
          />
        </div>
      </Modal>
    </div>
  );
}
