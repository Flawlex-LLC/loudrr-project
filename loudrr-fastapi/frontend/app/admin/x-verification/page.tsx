'use client';

import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Check,
  History,
  RefreshCcw,
  RotateCcw,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react';

import {
  adminApi,
  type AdminPage,
  type XVerificationPriorRequest,
  type XVerificationReviewRow,
  type XVerificationReviewStatus,
} from '@/lib/api';
import { Badge } from '@/components/admin/Badge';
import { Button } from '@/components/admin/Button';
import { CopyButton } from '@/components/admin/CopyButton';
import { EmptyState } from '@/components/admin/EmptyState';
import { Input } from '@/components/admin/Input';
import { Pagination } from '@/components/admin/Pagination';
import { TableScroll } from '@/components/admin/TableScroll';
import { TableSkeleton } from '@/components/admin/Skeleton';
import { absolute, karma, num, timeAgo } from '@/lib/admin/format';
import { useAdminSession } from '@/lib/admin/session';
import { useAdminResource } from '@/lib/admin/useAdminResource';
import { openLink } from '@/lib/telegram';
import { cn } from '@/lib/utils';

import { ActionDialog } from '../_review/ActionDialog';
import {
  ReasonFields,
  xVerificationRejectionPreview,
} from '../_review/ReasonFields';
import { EM_DASH, bareHandle, handleDiff } from '../_review/labels';

const PAGE_SIZE = 25;

const REJECT_PRESETS = [
  "We couldn't verify your X account",
  'That handle already belongs to another Loudrr account',
  'Reconnect with the account you signed up with',
];

const STATUS_TABS: Array<{ value: XVerificationReviewStatus; label: string }> = [
  { value: 'PENDING', label: 'Pending' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'REJECTED', label: 'Rejected' },
];

type Dialog =
  | { kind: 'approve'; row: XVerificationReviewRow }
  | { kind: 'reject'; row: XVerificationReviewRow }
  | null;

const isConflict = (message: string) => /^409\b/.test(message);

/** X resolves a profile from the immutable numeric id, so a rename still lands. */
const profileUrl = (row: { claimed_x_username: string; claimed_x_user_id: string }) =>
  row.claimed_x_user_id
    ? `https://x.com/i/user/${row.claimed_x_user_id}`
    : `https://x.com/${bareHandle(row.claimed_x_username)}`;

export default function AdminXVerificationPage() {
  const { refreshQueues } = useAdminSession();

  const [status, setStatus] = useState<XVerificationReviewStatus>('PENDING');
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);

  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);
  // Typed text survives a cancelled dialog.
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');

  useEffect(() => {
    const id = setTimeout(() => {
      setQ((prev) => {
        if (prev === search) return prev;
        setPage(1);
        return search;
      });
    }, 300);
    return () => clearTimeout(id);
  }, [search]);

  const { data, loading, error, reload, mutate } = useAdminResource<
    AdminPage<XVerificationReviewRow>
  >(
    () =>
      adminApi.listXVerifications({
        q,
        status,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    [q, status, page],
  );

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;
  const canAct = status === 'PENDING';

  const dropRow = useCallback(
    (id: string) => {
      mutate((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.filter((r) => r.id !== id),
              total: Math.max(0, prev.total - 1),
            }
          : prev,
      );
    },
    [mutate],
  );

  const openDialog = (next: NonNullable<Dialog>) => {
    setDialogError(null);
    setBlocked(false);
    setDialog(next);
  };

  const closeDialog = () => {
    if (busy) return;
    setDialog(null);
    setDialogError(null);
    setBlocked(false);
  };

  async function confirmApprove() {
    if (!dialog || dialog.kind !== 'approve') return;
    const { row } = dialog;
    setBusy(true);
    setDialogError(null);
    try {
      await adminApi.approveXVerification(row.id);
      dropRow(row.id);
      void refreshQueues();
      toast.success(`@${bareHandle(row.claimed_x_username)} adopted and verified`);
      setDialog(null);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Approve failed';
      // Stays on screen until the admin acts on it. The old flow toasted for
      // four seconds and left Confirm armed, so the obvious next move was to
      // fire the same irreversible action again.
      setDialogError(message);
      setBlocked(isConflict(message));
    } finally {
      setBusy(false);
    }
  }

  async function confirmReject() {
    if (!dialog || dialog.kind !== 'reject') return;
    const { row } = dialog;
    setBusy(true);
    setDialogError(null);
    try {
      await adminApi.rejectXVerification(row.id, reason.trim(), note.trim());
      dropRow(row.id);
      void refreshQueues();
      toast.success('Verification rejected', {
        description: `${row.user_telegram_username ? `@${row.user_telegram_username}` : 'The user'} stays unverified and can retry OAuth.`,
      });
      setReason('');
      setNote('');
      setDialog(null);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Reject failed';
      setDialogError(message);
      setBlocked(isConflict(message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-syne text-2xl font-bold tracking-tight">X Verification</h1>
          <p className="mt-1 text-sm text-zinc-400">
            {num(total)} request{total === 1 ? '' : 's'}
            {status === 'PENDING' ? ' awaiting an identity call' : ''}
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => void reload()} loading={loading}>
          <RefreshCcw size={12} aria-hidden />
          Refresh
        </Button>
      </div>

      <div role="tablist" aria-label="Request status" className="flex gap-1 overflow-x-auto">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.value}
            role="tab"
            type="button"
            aria-selected={status === tab.value}
            onClick={() => {
              setStatus(tab.value);
              setPage(1);
            }}
            className={cn(
              'min-h-[36px] shrink-0 rounded-lg border px-3 text-xs font-medium transition-colors',
              status === tab.value
                ? 'border-[#f95400]/40 bg-[#f95400]/15 text-white'
                : 'border-white/[0.08] bg-white/[0.03] text-zinc-400 hover:border-white/[0.16] hover:text-white',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="relative">
        <Search
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
        />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9"
          placeholder="Search either handle, or the user's Telegram/X username…"
          aria-label="Search verification requests"
        />
      </div>

      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-red-900/50 bg-red-950/30 p-4"
        >
          <div className="flex items-start gap-2.5">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-400" aria-hidden />
            <div className="text-sm">
              <p className="font-medium text-red-200">Couldn&apos;t load verification requests</p>
              <p className="mt-0.5 break-words text-xs text-red-300/80">{error}</p>
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={() => void reload()}>
            <RotateCcw size={12} aria-hidden />
            Try again
          </Button>
        </div>
      )}

      {loading && !data ? (
        <TableSkeleton rows={5} />
      ) : rows.length === 0 && !error ? (
        <EmptyState
          icon={ShieldCheck}
          title={q ? 'No matches' : status === 'PENDING' ? 'Nothing to review' : 'No requests here'}
          description={
            q
              ? 'No request matches that search.'
              : 'A request lands here when someone OAuths an X handle that differs from the one they signed up with, and confirms it is theirs.'
          }
        />
      ) : (
        <>
          <ul className="space-y-3 lg:hidden">
            {rows.map((row) => (
              <RequestCard
                key={row.id}
                row={row}
                canAct={canAct}
                onApprove={() => openDialog({ kind: 'approve', row })}
                onReject={() => openDialog({ kind: 'reject', row })}
              />
            ))}
          </ul>

          <div className="hidden overflow-hidden rounded-2xl border border-white/[0.06] lg:block">
            {/* See the waitlist table: this has to cover the columns' real
                min-content width, or the action column is clipped with no
                scrollbar to reach it. */}
            <TableScroll minWidth={1180}>
              <div className="max-h-[68vh] overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10 bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-400 shadow-[0_1px_0_rgba(255,255,255,0.06)]">
                    <tr>
                      <th scope="col" className="px-4 py-3 font-semibold">User</th>
                      <th scope="col" className="px-4 py-3 font-semibold">Signed up as</th>
                      <th scope="col" className="px-4 py-3 font-semibold">Wants to claim</th>
                      <th scope="col" className="px-4 py-3 font-semibold">History</th>
                      <th scope="col" className="px-4 py-3 font-semibold">
                        {status === 'PENDING' ? 'Requested' : 'Reviewed'}
                      </th>
                      <th scope="col" className="px-4 py-3 text-right font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {rows.map((row) => (
                      <RequestRow
                        key={row.id}
                        row={row}
                        canAct={canAct}
                        status={status}
                        onApprove={() => openDialog({ kind: 'approve', row })}
                        onReject={() => openDialog({ kind: 'reject', row })}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </TableScroll>
          </div>

          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}

      {/* ---- approve ---- */}
      <ActionDialog
        open={dialog?.kind === 'approve'}
        onClose={closeDialog}
        size="lg"
        title="Approve this verification?"
        confirmLabel="Adopt the handle"
        busy={busy}
        error={dialogError}
        blocked={blocked || Boolean(dialog?.kind === 'approve' && dialog.row.claimed_handle_taken_by)}
        onConfirm={confirmApprove}
      >
        {dialog?.kind === 'approve' && (
          <>
            <IdentityPanel row={dialog.row} />
            {dialog.row.claimed_handle_taken_by ? (
              <div className="rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-200">
                @{bareHandle(dialog.row.claimed_x_username)} is already in use by{' '}
                <span className="font-medium">
                  {holderLabel(dialog.row.claimed_handle_taken_by)}
                </span>
                . Approving would fail with a 409 — reject this request, or resolve the other
                account first.
              </div>
            ) : (
              <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs text-zinc-400">
                <p className="mb-1.5 font-semibold text-zinc-200">This will:</p>
                <ul className="list-inside list-disc space-y-1">
                  <li>
                    move the account&apos;s handle to{' '}
                    <span className="text-zinc-200">@{bareHandle(dialog.row.claimed_x_username)}</span>
                  </li>
                  <li>mark them X-verified, so they can earn karma</li>
                  <li>send them the &ldquo;you&rsquo;re verified&rdquo; Telegram message</li>
                </ul>
                <p className="mt-2 text-amber-300/90">
                  There is no un-approve in the panel.
                </p>
              </div>
            )}
          </>
        )}
      </ActionDialog>

      {/* ---- reject ---- */}
      <ActionDialog
        open={dialog?.kind === 'reject'}
        onClose={closeDialog}
        tone="danger"
        size="lg"
        title="Reject this verification?"
        confirmLabel="Reject"
        warning="They get a Telegram message right away. The request is closed for good — they can run the X OAuth flow again from the mini-app."
        busy={busy}
        error={dialogError}
        blocked={blocked}
        onConfirm={confirmReject}
      >
        {dialog?.kind === 'reject' && (
          <>
            <IdentityPanel row={dialog.row} />
            <ReasonFields
              idPrefix="xv-reject"
              reason={reason}
              onReasonChange={setReason}
              note={note}
              onNoteChange={setNote}
              presets={REJECT_PRESETS}
              subject="user"
              disabled={busy}
              previewFor={(value) =>
                xVerificationRejectionPreview(
                  dialog.row.submitted_x_username,
                  dialog.row.claimed_x_username,
                  value,
                )
              }
            />
            <p className="text-[11px] text-zinc-500">
              They stay unverified with the handle they signed up with, and can run the
              OAuth flow again.
            </p>
          </>
        )}
      </ActionDialog>
    </div>
  );
}

/* ========================================================================== */

/**
 * The claimed handle with the characters that differ from the submitted one
 * picked out.
 *
 * The old column showed a "MISMATCH" badge on every row — which is every row
 * in this queue by definition, so it carried no information. What a reviewer
 * needs is WHERE the two differ: one seeded pair is @seed_yusuf vs
 * @seed_yusuf_, a single trailing underscore.
 */
function HandleDiff({ submitted, claimed }: { submitted: string; claimed: string }) {
  const bare = bareHandle(claimed);
  if (!bare) return <span className="text-zinc-600">{EM_DASH}</span>;
  if (!bareHandle(submitted)) {
    return <span className="text-zinc-100">@{bare}</span>;
  }
  const { shared, rest } = handleDiff(submitted, claimed);
  return (
    <span className="font-mono">
      <span className="text-zinc-500">@{shared}</span>
      {rest && (
        <span className="rounded-sm bg-amber-500/20 px-0.5 font-semibold text-amber-200">
          {rest}
        </span>
      )}
    </span>
  );
}

/** A handle the user never had renders as "not set", not a bare "@". */
function SubmittedHandle({ value }: { value: string }) {
  const bare = bareHandle(value);
  if (!bare) {
    return (
      <span
        className="text-xs italic text-zinc-500"
        title="This account had no X handle when the request was opened"
      >
        not set
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => openLink(`https://x.com/${bare}`)}
      className="font-mono text-zinc-300 transition-colors hover:text-[#f95400] hover:underline"
    >
      @{bare}
    </button>
  );
}

function UserCell({ row }: { row: XVerificationReviewRow }) {
  const label = row.user_telegram_username
    ? `@${row.user_telegram_username}`
    : row.user_display_name || (row.user_telegram_id ? `#${row.user_telegram_id}` : EM_DASH);
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="truncate text-white">{label}</span>
        {row.user_is_banned && (
          <Badge tone="danger">
            <Ban size={9} aria-hidden />
            banned
          </Badge>
        )}
        {row.user_x_verified && <Badge tone="success">verified</Badge>}
      </div>
      <p className="mt-0.5 truncate text-[11px] text-zinc-500">
        {row.user_score !== null && (
          <>
            {num(Math.round(row.user_score))} · {row.user_tier} ·{' '}
          </>
        )}
        {karma(row.user_credits)} karma · joined {timeAgo(row.user_created_at)}
      </p>
    </div>
  );
}

function PriorRequests({ items }: { items: XVerificationPriorRequest[] }) {
  if (items.length === 0) {
    return <span className="text-[11px] text-zinc-600">first request</span>;
  }
  const rejected = items.filter((r) => r.status === 'REJECTED').length;
  return (
    <div className="min-w-0">
      <span
        className={cn(
          'inline-flex items-center gap-1 text-[11px]',
          rejected > 0 ? 'text-amber-300' : 'text-zinc-400',
        )}
      >
        <History size={11} aria-hidden />
        {items.length} prior{rejected > 0 && ` · ${rejected} rejected`}
      </span>
      {items[0].admin_notes && (
        <p className="mt-0.5 max-w-[220px] truncate text-[11px] text-zinc-500" title={items[0].admin_notes}>
          &ldquo;{items[0].admin_notes}&rdquo;
        </p>
      )}
    </div>
  );
}

/** "another Loudrr account (Telegram @x)" — never just the handle again. */
function holderLabel(taken: NonNullable<XVerificationReviewRow['claimed_handle_taken_by']>) {
  if (taken.telegram_username) return `another Loudrr account (Telegram @${taken.telegram_username})`;
  return `another Loudrr account (${taken.user_id})`;
}

function takenTitle(row: XVerificationReviewRow) {
  const taken = row.claimed_handle_taken_by;
  if (!taken) return undefined;
  return `@${bareHandle(row.claimed_x_username)} is already in use by ${holderLabel(taken)} — approving would fail`;
}

function TakenWarning({ row }: { row: XVerificationReviewRow }) {
  if (!row.claimed_handle_taken_by) return null;
  return (
    <Badge tone="danger" className="normal-case">
      <span title={takenTitle(row)}>handle taken</span>
    </Badge>
  );
}

function ApproveButton({
  row,
  onApprove,
  className,
}: {
  row: XVerificationReviewRow;
  onApprove: () => void;
  className?: string;
}) {
  const taken = row.claimed_handle_taken_by;
  return (
    <Button
      size="sm"
      variant="success"
      onClick={onApprove}
      disabled={Boolean(taken)}
      className={className}
      title={takenTitle(row)}
    >
      <Check size={12} aria-hidden />
      Approve
    </Button>
  );
}

function RequestRow({
  row,
  canAct,
  status,
  onApprove,
  onReject,
}: {
  row: XVerificationReviewRow;
  canAct: boolean;
  status: XVerificationReviewStatus;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <tr className="bg-[#111] transition-colors hover:bg-[#161616]">
      <td className="max-w-[240px] px-4 py-3">
        <UserCell row={row} />
      </td>
      <td className="px-4 py-3">
        <SubmittedHandle value={row.submitted_x_username} />
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => openLink(profileUrl(row))}
            aria-label={`Open the claimed X account @${bareHandle(row.claimed_x_username)}`}
            className="transition-colors hover:underline"
          >
            <HandleDiff submitted={row.submitted_x_username} claimed={row.claimed_x_username} />
          </button>
          <TakenWarning row={row} />
        </div>
        {row.claimed_x_user_id && (
          <p className="mt-0.5 font-mono text-[10px] text-zinc-600">id {row.claimed_x_user_id}</p>
        )}
      </td>
      <td className="px-4 py-3">
        <PriorRequests items={row.prior_requests} />
      </td>
      <td className="px-4 py-3 text-xs text-zinc-400">
        <span
          title={absolute(status === 'PENDING' ? row.created_at : row.reviewed_at, { seconds: true })}
        >
          {timeAgo(status === 'PENDING' ? row.created_at : row.reviewed_at)}
        </span>
        {status !== 'PENDING' && row.admin_notes && (
          <p className="mt-0.5 max-w-[200px] truncate text-[11px] text-zinc-500" title={row.admin_notes}>
            &ldquo;{row.admin_notes}&rdquo;
          </p>
        )}
      </td>
      <td className="px-4 py-3">
        {canAct ? (
          <div className="flex justify-end gap-1.5">
            <ApproveButton row={row} onApprove={onApprove} />
            <Button size="sm" variant="danger" onClick={onReject}>
              <X size={12} aria-hidden />
              Reject
            </Button>
          </div>
        ) : (
          <div className="text-right">
            <Badge tone={row.status === 'APPROVED' ? 'success' : 'danger'}>{row.status}</Badge>
          </div>
        )}
      </td>
    </tr>
  );
}

function RequestCard({
  row,
  canAct,
  onApprove,
  onReject,
}: {
  row: XVerificationReviewRow;
  canAct: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <li className="rounded-2xl border border-white/[0.06] bg-[#111] p-4">
      <UserCell row={row} />

      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.06] bg-[#0a0a0a] px-3 py-2 text-sm">
        <SubmittedHandle value={row.submitted_x_username} />
        <ArrowRight size={13} className="text-zinc-600" aria-hidden />
        <button
          type="button"
          onClick={() => openLink(profileUrl(row))}
          aria-label={`Open the claimed X account @${bareHandle(row.claimed_x_username)}`}
        >
          <HandleDiff submitted={row.submitted_x_username} claimed={row.claimed_x_username} />
        </button>
        <TakenWarning row={row} />
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs">
        <dt className="text-zinc-500">History</dt>
        <dd className="text-right">
          <PriorRequests items={row.prior_requests} />
        </dd>
        <dt className="text-zinc-500">{row.status === 'PENDING' ? 'Requested' : 'Reviewed'}</dt>
        <dd className="text-right text-zinc-300">
          {timeAgo(row.status === 'PENDING' ? row.created_at : row.reviewed_at)}
        </dd>
      </dl>

      {row.claimed_x_user_id && (
        <div className="mt-2 flex items-center gap-2">
          <span className="font-mono text-[10px] text-zinc-600">id {row.claimed_x_user_id}</span>
          <CopyButton value={row.claimed_x_user_id} className="h-6" />
        </div>
      )}

      {canAct ? (
        <div className="mt-3 grid grid-cols-2 gap-2 [&_button]:min-h-[44px] [&_button]:w-full">
          <ApproveButton row={row} onApprove={onApprove} />
          <Button size="sm" variant="danger" onClick={onReject}>
            <X size={12} aria-hidden />
            Reject
          </Button>
        </div>
      ) : (
        <div className="mt-3">
          <Badge tone={row.status === 'APPROVED' ? 'success' : 'danger'}>{row.status}</Badge>
          {row.admin_notes && (
            <p className="mt-2 rounded-lg bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-zinc-400">
              &ldquo;{row.admin_notes}&rdquo;
            </p>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * Everything the identity call needs, in one panel: who is asking, what they
 * hold today, what they want, and what the team said to them last time.
 */
function IdentityPanel({ row }: { row: XVerificationReviewRow }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
        <dl className="grid grid-cols-[110px_1fr] gap-y-1.5">
          <dt className="text-zinc-500">User</dt>
          <dd className="flex flex-wrap items-center gap-1.5 text-zinc-100">
            {row.user_telegram_username
              ? `@${row.user_telegram_username}`
              : row.user_display_name || `#${row.user_telegram_id ?? EM_DASH}`}
            {row.user_is_banned && <Badge tone="danger">banned</Badge>}
          </dd>

          <dt className="text-zinc-500">Standing</dt>
          <dd className="text-zinc-100">
            {row.user_score !== null ? `${num(Math.round(row.user_score))} · ${row.user_tier}` : 'unscored'}
            <span className="text-zinc-500">
              {' '}
              · {karma(row.user_credits)} karma · joined {absolute(row.user_created_at)}
            </span>
          </dd>

          <dt className="text-zinc-500">Holds today</dt>
          <dd className="font-mono text-zinc-100">
            {row.user_x_username ? `@${bareHandle(row.user_x_username)}` : 'no handle'}
          </dd>

          <dt className="text-zinc-500">Signed up as</dt>
          <dd className="font-mono text-zinc-100">
            {bareHandle(row.submitted_x_username)
              ? `@${bareHandle(row.submitted_x_username)}`
              : 'not set'}
          </dd>

          <dt className="text-zinc-500">Claiming</dt>
          <dd>
            <HandleDiff submitted={row.submitted_x_username} claimed={row.claimed_x_username} />
          </dd>

          <dt className="text-zinc-500">Claimed X id</dt>
          <dd className="flex items-center gap-2">
            <span className="font-mono text-zinc-100">{row.claimed_x_user_id || EM_DASH}</span>
            {row.claimed_x_user_id && <CopyButton value={row.claimed_x_user_id} className="h-6" />}
          </dd>
        </dl>

        <button
          type="button"
          onClick={() => openLink(profileUrl(row))}
          className="mt-2 text-[11px] text-[#f95400] hover:underline"
        >
          Open the claimed account on X ↗
        </button>
      </div>

      {row.prior_requests.length > 0 && (
        <div className="rounded-lg border border-amber-900/40 bg-amber-950/20 p-3 text-xs">
          <p className="mb-1.5 flex items-center gap-1.5 font-semibold text-amber-200">
            <History size={12} aria-hidden />
            {row.prior_requests.length} earlier request
            {row.prior_requests.length === 1 ? '' : 's'} from this account
          </p>
          <ul className="space-y-1.5">
            {row.prior_requests.map((prior) => (
              <li key={prior.id} className="text-zinc-300">
                <span className="font-mono">@{bareHandle(prior.claimed_x_username) || EM_DASH}</span>{' '}
                <span
                  className={cn(
                    'text-[10px] font-semibold uppercase',
                    prior.status === 'REJECTED' ? 'text-red-300' : 'text-emerald-300',
                  )}
                >
                  {prior.status}
                </span>{' '}
                <span className="text-zinc-500">{timeAgo(prior.created_at)}</span>
                {prior.admin_notes && (
                  <p className="text-[11px] text-zinc-400">&ldquo;{prior.admin_notes}&rdquo;</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
