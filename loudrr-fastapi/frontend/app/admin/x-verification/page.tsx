'use client';

import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Check, X, RefreshCcw, ShieldCheck, AlertTriangle } from 'lucide-react';
import { adminApi, PendingXVerification } from '@/lib/api';
import { Button } from '@/components/admin/Button';
import { Modal } from '@/components/admin/Modal';
import { Textarea } from '@/components/admin/Input';
import { EmptyState } from '@/components/admin/EmptyState';
import { Badge } from '@/components/admin/Badge';
import { TableSkeleton } from '@/components/admin/Skeleton';

export default function AdminXVerificationPage() {
  const [rows, setRows] = useState<PendingXVerification[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rejectTarget, setRejectTarget] = useState<PendingXVerification | null>(null);
  const [rejectNotes, setRejectNotes] = useState('');
  const [approveTarget, setApproveTarget] = useState<PendingXVerification | null>(null);

  async function load() {
    setError(null);
    try {
      setRows(await adminApi.pendingXVerifications(200));
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      toast.error('Failed to load verifications', { description: msg });
    }
  }

  useEffect(() => { load(); }, []);

  async function confirmApprove() {
    if (!approveTarget) return;
    setBusyId(approveTarget.id);
    try {
      await adminApi.approveXVerification(approveTarget.id);
      toast.success('Verification approved', {
        description: `Adopted @${approveTarget.claimed_x_username} for ${approveTarget.user_telegram_username ? '@' + approveTarget.user_telegram_username : 'this user'}.`,
      });
      setApproveTarget(null);
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
      await adminApi.rejectXVerification(rejectTarget.id, rejectNotes);
      toast.success('Verification rejected', {
        description: rejectNotes ? `Notes: ${rejectNotes}` : 'No notes attached.',
      });
      setRejectTarget(null);
      setRejectNotes('');
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
          <h1 className="font-syne text-2xl font-bold tracking-tight">Pending X Verifications</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {rows === null ? 'Loading…' : rows.length === 0 ? 'Nothing to review.' : `${rows.length} request${rows.length === 1 ? '' : 's'} awaiting review`}
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={load} disabled={rows === null}>
          <RefreshCcw size={12} />
          Refresh
        </Button>
      </div>

      {error && <div className="rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">{error}</div>}

      {rows === null ? (
        <TableSkeleton rows={5} />
      ) : rows.length === 0 ? (
        <EmptyState icon={ShieldCheck} title="No pending verifications" description="X OAuth handle mismatches will appear here for manual review." />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-white/[0.06]">
          <table className="w-full text-sm">
            <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-4 py-3 font-semibold">User (Telegram)</th>
                <th className="px-4 py-3 font-semibold">Submitted handle</th>
                <th className="px-4 py-3 font-semibold">Claimed (OAuth)</th>
                <th className="px-4 py-3 font-semibold">Created</th>
                <th className="px-4 py-3 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {rows.map((r) => {
                const submittedAt = r.submitted_x_username.replace(/^@/, '');
                const claimedAt = r.claimed_x_username.replace(/^@/, '');
                return (
                  <tr key={r.id} className="bg-[#111] hover:bg-[#161616] transition-colors">
                    <td className="px-4 py-3">
                      {r.user_telegram_username ? (
                        <span className="text-white">@{r.user_telegram_username}</span>
                      ) : (
                        <span className="text-zinc-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {submittedAt ? (
                        <a
                          href={`https://x.com/${submittedAt}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-zinc-300 hover:text-[#f95400] hover:underline"
                        >
                          @{submittedAt}
                        </a>
                      ) : (
                        <span className="text-zinc-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="inline-flex items-center gap-1.5">
                        <a
                          href={`https://x.com/${claimedAt}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-zinc-300 hover:text-[#f95400] hover:underline"
                        >
                          @{claimedAt}
                        </a>
                        {submittedAt && submittedAt.toLowerCase() !== claimedAt.toLowerCase() && (
                          <Badge tone="warning">mismatch</Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-500">
                      {r.created_at ? new Date(r.created_at).toLocaleString() : '—'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex gap-1.5">
                        <Button size="sm" variant="success" disabled={busyId === r.id} onClick={() => setApproveTarget(r)}>
                          <Check size={12} />
                          Approve
                        </Button>
                        <Button size="sm" variant="danger" disabled={busyId === r.id} onClick={() => { setRejectTarget(r); setRejectNotes(''); }}>
                          <X size={12} />
                          Reject
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={!!approveTarget}
        onClose={() => setApproveTarget(null)}
        title="Approve X verification"
        description="The user will adopt the claimed handle and be marked verified. If the handle is already taken by another user, this will fail."
        footer={
          <>
            <Button variant="secondary" onClick={() => setApproveTarget(null)}>Cancel</Button>
            <Button variant="success" loading={busyId === approveTarget?.id} onClick={confirmApprove}>
              <Check size={14} />
              Confirm Approve
            </Button>
          </>
        }
      >
        {approveTarget && (
          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3">
              <div className="grid grid-cols-[100px_1fr] gap-y-1.5">
                <span className="text-zinc-500 text-xs">User</span>
                <span className="text-zinc-200">@{approveTarget.user_telegram_username}</span>
                <span className="text-zinc-500 text-xs">Submitted</span>
                <span className="text-zinc-200">@{approveTarget.submitted_x_username.replace(/^@/, '')}</span>
                <span className="text-zinc-500 text-xs">Claimed</span>
                <span className="text-white font-medium">@{approveTarget.claimed_x_username.replace(/^@/, '')}</span>
              </div>
            </div>
            <div className="flex items-start gap-2 rounded-lg border border-amber-900/40 bg-amber-950/20 p-3 text-xs text-amber-300">
              <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
              <span>This is irreversible from the panel. Use the SQLAdmin browser to revert if needed.</span>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={!!rejectTarget}
        onClose={() => { setRejectTarget(null); setRejectNotes(''); }}
        title="Reject X verification"
        description="The user will stay unverified and pending review will be cleared. They can retry the OAuth flow."
        footer={
          <>
            <Button variant="secondary" onClick={() => setRejectTarget(null)}>Cancel</Button>
            <Button variant="danger" loading={busyId === rejectTarget?.id} onClick={confirmReject}>
              <X size={14} />
              Confirm Reject
            </Button>
          </>
        }
      >
        {rejectTarget && (
          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
              <div className="grid grid-cols-[100px_1fr] gap-y-1.5">
                <span className="text-zinc-500">User</span><span className="text-zinc-200">@{rejectTarget.user_telegram_username}</span>
                <span className="text-zinc-500">Claimed handle</span><span className="text-zinc-200">@{rejectTarget.claimed_x_username.replace(/^@/, '')}</span>
              </div>
            </div>
            <Textarea
              label="Notes (optional, internal)"
              placeholder="e.g. doesn't match the X profile they screenshot'd in support…"
              value={rejectNotes}
              onChange={(e) => setRejectNotes(e.target.value)}
              rows={3}
              hint="Stored in audit_logs; the user doesn't see it."
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
