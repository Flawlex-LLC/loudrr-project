'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Info,
  RefreshCcw,
  RotateCcw,
  Search,
  Undo2,
  UserCheck,
  Users,
  X,
} from 'lucide-react';

import {
  adminApi,
  type AdminPage,
  type WaitlistReviewRow,
  type WaitlistReviewStatus,
} from '@/lib/api';
import { Badge } from '@/components/admin/Badge';
import { Button } from '@/components/admin/Button';
import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { CopyButton } from '@/components/admin/CopyButton';
import { EmptyState } from '@/components/admin/EmptyState';
import { Input } from '@/components/admin/Input';
import { Modal } from '@/components/admin/Modal';
import { Pagination } from '@/components/admin/Pagination';
import { TableScroll } from '@/components/admin/TableScroll';
import { TableSkeleton } from '@/components/admin/Skeleton';
import { absolute, num, timeAgo } from '@/lib/admin/format';
import { useAdminSession } from '@/lib/admin/session';
import { useAdminResource } from '@/lib/admin/useAdminResource';
import { openLink } from '@/lib/telegram';
import { cn } from '@/lib/utils';

import { ActionDialog } from '../_review/ActionDialog';
import {
  ReasonFields,
  waitlistRejectionPreview,
} from '../_review/ReasonFields';
import {
  EM_DASH,
  accountAge,
  bareHandle,
  compact,
  identityLabel,
  nicheLabel,
  regionLabel,
} from '../_review/labels';

const PAGE_SIZE = 25;

/** The rejections that come up every day, as one-tap public reasons. */
const REJECT_PRESETS = [
  'Not a fit for the beta right now',
  "We couldn't verify your X account",
  'Region not open yet',
];

const STATUS_TABS: Array<{ value: WaitlistReviewStatus; label: string }> = [
  { value: 'submitted', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
];

type SortKey = 'created' | 'score' | 'tier';

interface QueryState {
  q: string;
  status: WaitlistReviewStatus;
  sort: SortKey;
  dir: 'asc' | 'desc';
  region: string;
  niche: string;
  hasScore: '' | 'yes' | 'no';
  page: number;
}

const INITIAL_QUERY: QueryState = {
  q: '',
  status: 'submitted',
  sort: 'created',
  dir: 'asc', // oldest first — FIFO is the fair default for a queue
  region: '',
  niche: '',
  hasScore: '',
  page: 1,
};

type Dialog =
  | { kind: 'approve'; rows: WaitlistReviewRow[] }
  | { kind: 'reject'; rows: WaitlistReviewRow[] }
  | { kind: 'reopen'; rows: WaitlistReviewRow[] }
  | null;

/** A 409 means the row's state moved on — retrying can only fail again. */
const isConflict = (message: string) => /^409\b/.test(message);

export default function AdminWaitlistPage() {
  const { refreshQueues } = useAdminSession();

  const [query, setQuery] = useState<QueryState>(INITIAL_QUERY);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [detail, setDetail] = useState<WaitlistReviewRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);

  // Typed text survives a cancelled dialog — an admin who closes the modal to
  // re-read a profile shouldn't come back to an empty box.
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');

  // Any filter change resets to page 1; only an explicit page move keeps it.
  const patch = useCallback((next: Partial<QueryState>) => {
    setQuery((prev) => ({ ...prev, ...next, page: next.page ?? 1 }));
    setSelected([]);
  }, []);

  // Debounce the search box: one request per pause, not per keystroke.
  useEffect(() => {
    const id = setTimeout(() => {
      setQuery((prev) => (prev.q === search ? prev : { ...prev, q: search, page: 1 }));
    }, 300);
    return () => clearTimeout(id);
  }, [search]);

  const { q, status, sort, dir, region, niche, hasScore, page } = query;

  const { data, loading, error, reload, mutate } = useAdminResource<
    AdminPage<WaitlistReviewRow>
  >(
    () =>
      adminApi.listWaitlist({
        q,
        status,
        sort,
        dir,
        region,
        niche,
        hasScore: hasScore === '' ? null : hasScore === 'yes',
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    [q, status, sort, dir, region, niche, hasScore, page],
  );

  const facets = useAdminResource(() => adminApi.waitlistFacets(), []);

  // Memoised so the `[]` default isn't a new array identity every render —
  // which would re-run the selection memo below on each one.
  const rows = useMemo(() => data?.items ?? [], [data]);
  const total = data?.total ?? 0;
  const filtersActive = Boolean(q || region || niche || hasScore);

  const selectedRows = useMemo(
    () => rows.filter((r) => selected.includes(r.id)),
    [rows, selected],
  );

  // ---- local list surgery -------------------------------------------------
  // Refetching after every decision made the remaining rows jump under the
  // cursor; drop the decided row instead and keep the page still.
  const dropRows = useCallback(
    (ids: string[]) => {
      mutate((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.filter((r) => !ids.includes(r.id)),
              total: Math.max(0, prev.total - ids.length),
            }
          : prev,
      );
      setSelected((prev) => prev.filter((id) => !ids.includes(id)));
    },
    [mutate],
  );

  const patchRow = useCallback(
    (row: WaitlistReviewRow) => {
      mutate((prev) =>
        prev
          ? { ...prev, items: prev.items.map((r) => (r.id === row.id ? row : r)) }
          : prev,
      );
      setDetail((prev) => (prev && prev.id === row.id ? row : prev));
    },
    [mutate],
  );

  // ---- dialogs ------------------------------------------------------------
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

  async function runAll(
    targets: WaitlistReviewRow[],
    action: (row: WaitlistReviewRow) => Promise<unknown>,
  ) {
    const done: string[] = [];
    const failures: Array<{ row: WaitlistReviewRow; message: string }> = [];
    // Sequential on purpose: these create users, whitelist them and send DMs.
    for (const row of targets) {
      try {
        await action(row);
        done.push(row.id);
      } catch (e) {
        failures.push({ row, message: e instanceof Error ? e.message : 'Failed' });
      }
    }
    return { done, failures };
  }

  function reportFailures(
    failures: Array<{ row: WaitlistReviewRow; message: string }>,
    doneCount: number,
  ) {
    if (failures.length === 1 && doneCount === 0) {
      // One target, one reason — no tally to read.
      setDialogError(failures[0].message);
    } else {
      const lines = failures
        .slice(0, 6)
        .map((f) => `${identityLabel(f.row)} — ${f.message}`);
      if (failures.length > lines.length) {
        lines.push(`…and ${failures.length - lines.length} more`);
      }
      const header =
        doneCount > 0
          ? `${doneCount} succeeded, ${failures.length} failed:`
          : `${failures.length} failed:`;
      setDialogError([header, ...lines].join('\n'));
    }
    // A single row that 409'd can't be retried — the decision already exists.
    setBlocked(failures.length === 1 && doneCount === 0 && isConflict(failures[0].message));
  }

  async function confirmApprove() {
    if (!dialog || dialog.kind !== 'approve') return;
    setBusy(true);
    setDialogError(null);
    const { done, failures } = await runAll(dialog.rows, (row) =>
      adminApi.approveWaitlist(row.id),
    );
    setBusy(false);

    if (done.length) {
      dropRows(done);
      void refreshQueues();
      toast.success(
        done.length === 1
          ? `Approved ${identityLabel(dialog.rows[0])}`
          : `Approved ${done.length} applicants`,
      );
    }
    if (failures.length) {
      reportFailures(failures, done.length);
      return;
    }
    setDialog(null);
  }

  async function confirmReject() {
    if (!dialog || dialog.kind !== 'reject') return;
    setBusy(true);
    setDialogError(null);
    const { done, failures } = await runAll(dialog.rows, (row) =>
      adminApi.rejectWaitlist(row.id, reason.trim(), note.trim()),
    );
    setBusy(false);

    if (done.length) {
      dropRows(done);
      void refreshQueues();
      toast.success(
        done.length === 1
          ? `Rejected ${identityLabel(dialog.rows[0])}`
          : `Rejected ${done.length} applicants`,
      );
    }
    if (failures.length) {
      reportFailures(failures, done.length);
      return;
    }
    setReason('');
    setNote('');
    setDialog(null);
  }

  async function confirmReopen() {
    if (!dialog || dialog.kind !== 'reopen') return;
    setBusy(true);
    setDialogError(null);
    const { done, failures } = await runAll(dialog.rows, (row) =>
      adminApi.reopenWaitlist(row.id, note.trim()),
    );
    setBusy(false);

    if (done.length) {
      dropRows(done);
      void refreshQueues();
      toast.success(
        done.length === 1
          ? `${identityLabel(dialog.rows[0])} is back in the queue`
          : `${done.length} entries back in the queue`,
      );
    }
    if (failures.length) {
      reportFailures(failures, done.length);
      return;
    }
    setNote('');
    setDialog(null);
  }

  async function refreshScore(row: WaitlistReviewRow) {
    setRefreshingId(row.id);
    try {
      const result = await adminApi.refreshWaitlistScore(row.id);
      patchRow(result.entry);
      if (result.queued) {
        toast.success('Score fetch queued', {
          description: 'It lands in the background — refresh the list in a moment.',
        });
      } else if (result.result === 'found') {
        toast.success(`Score updated: ${num(result.entry.score ?? 0)}`);
      } else if (result.result === 'not_found') {
        toast.warning('The provider has no score for that handle.');
      } else {
        toast.warning('The score provider is unavailable right now — try later.');
      }
    } catch (e) {
      toast.error('Refresh failed', {
        description: e instanceof Error ? e.message : 'Unknown error',
      });
    } finally {
      setRefreshingId(null);
    }
  }

  const toggleSort = (key: SortKey) => {
    if (sort === key) {
      patch({ sort: key, dir: dir === 'asc' ? 'desc' : 'asc', page });
    } else {
      // score/tier are most useful highest-first; a date queue is oldest-first
      patch({ sort: key, dir: key === 'created' ? 'asc' : 'desc' });
    }
  };

  const allOnPageSelected = rows.length > 0 && selected.length === rows.length;
  const canAct = status === 'submitted';

  return (
    <div className="space-y-5">
      <Header
        status={status}
        total={total}
        loading={loading}
        filtersActive={filtersActive}
        onReload={() => void reload()}
      />

      <Tabs status={status} onChange={(next) => patch({ status: next })} />

      <Filters
        search={search}
        onSearch={setSearch}
        region={region}
        niche={niche}
        hasScore={hasScore}
        regions={facets.data?.regions ?? []}
        niches={facets.data?.niches ?? []}
        onPatch={patch}
        onClear={() => {
          setSearch('');
          setQuery({ ...INITIAL_QUERY, status });
          setSelected([]);
        }}
        filtersActive={filtersActive}
      />

      {error && (
        <ErrorState message={error} onRetry={() => void reload()} hasRows={rows.length > 0} />
      )}

      {canAct && selected.length > 0 && (
        <BulkBar
          count={selected.length}
          onApprove={() => openDialog({ kind: 'approve', rows: selectedRows })}
          onReject={() => openDialog({ kind: 'reject', rows: selectedRows })}
          onClear={() => setSelected([])}
        />
      )}

      {loading && !data ? (
        <TableSkeleton rows={6} />
      ) : rows.length === 0 && !error ? (
        <EmptyState
          icon={UserCheck}
          title={filtersActive ? 'No matches' : emptyTitle(status)}
          description={
            filtersActive
              ? 'No applicants match these filters. Clear them to see the whole queue.'
              : emptyDescription(status)
          }
        />
      ) : (
        <>
          {/* ---- phone / narrow: cards. The table's action column used to be
               physically unreachable below ~1150px — clipped by an
               overflow-hidden wrapper with no way to scroll to it. ---- */}
          <ul className="space-y-3 md:hidden">
            {rows.map((row) => (
              <RowCard
                key={row.id}
                row={row}
                canAct={canAct}
                selected={selected.includes(row.id)}
                onToggle={() => toggleOne(row.id, setSelected)}
                onApprove={() => openDialog({ kind: 'approve', rows: [row] })}
                onReject={() => openDialog({ kind: 'reject', rows: [row] })}
                onReopen={() => openDialog({ kind: 'reopen', rows: [row] })}
                onDetail={() => setDetail(row)}
                onRefreshScore={() => void refreshScore(row)}
                refreshing={refreshingId === row.id}
              />
            ))}
          </ul>

          <div className="hidden overflow-hidden rounded-2xl border border-white/[0.06] md:block">
            {/* minWidth must cover what the columns actually need: the inner
                sizing div is what the scroll container measures, so a value
                below the table's min-content width clips the action column
                with no scrollbar to reach it — the original bug, one layer
                further in. */}
            <TableScroll minWidth={1100}>
              {/* The inner vertical scroller is what the sticky header sticks
                  to — 25 rows is taller than a laptop viewport. */}
              <div className="max-h-[68vh] overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10 bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-400 shadow-[0_1px_0_rgba(255,255,255,0.06)]">
                    <tr>
                      {canAct && (
                        <th scope="col" className="w-10 px-3 py-3">
                          <input
                            type="checkbox"
                            checked={allOnPageSelected}
                            aria-label="Select all applicants on this page"
                            onChange={(e) =>
                              setSelected(e.target.checked ? rows.map((r) => r.id) : [])
                            }
                            className="h-4 w-4 accent-[#f95400]"
                          />
                        </th>
                      )}
                      <th scope="col" className="px-3 py-3 font-semibold">Applicant</th>
                      <th scope="col" className="px-3 py-3 font-semibold">X handle</th>
                      <SortHeader
                        label="Score"
                        active={sort === 'score' || sort === 'tier'}
                        dir={dir}
                        onClick={() => toggleSort('score')}
                        className="text-right"
                      />
                      <th scope="col" className="px-3 py-3 font-semibold">Region / Niche</th>
                      <th scope="col" className="px-3 py-3 font-semibold">Referral</th>
                      <SortHeader
                        label={status === 'submitted' ? 'Submitted' : 'Decided'}
                        active={sort === 'created'}
                        dir={dir}
                        onClick={() => toggleSort('created')}
                      />
                      <th scope="col" className="px-3 py-3 text-right font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {rows.map((row) => (
                      <Row
                        key={row.id}
                        row={row}
                        canAct={canAct}
                        status={status}
                        selected={selected.includes(row.id)}
                        onToggle={() => toggleOne(row.id, setSelected)}
                        onApprove={() => openDialog({ kind: 'approve', rows: [row] })}
                        onReject={() => openDialog({ kind: 'reject', rows: [row] })}
                        onReopen={() => openDialog({ kind: 'reopen', rows: [row] })}
                        onDetail={() => setDetail(row)}
                        onRefreshScore={() => void refreshScore(row)}
                        refreshing={refreshingId === row.id}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </TableScroll>
          </div>

          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={(next) => {
              setSelected([]);
              setQuery((prev) => ({ ...prev, page: next }));
            }}
          />
        </>
      )}

      {/* ---- approve ---- */}
      <ActionDialog
        open={dialog?.kind === 'approve'}
        onClose={closeDialog}
        title={
          dialog?.kind === 'approve' && dialog.rows.length > 1
            ? `Approve ${dialog.rows.length} applicants?`
            : 'Approve this applicant?'
        }
        confirmLabel={
          dialog?.kind === 'approve' && dialog.rows.length > 1
            ? `Approve ${dialog.rows.length}`
            : 'Approve'
        }
        busy={busy}
        error={dialogError}
        blocked={blocked}
        onConfirm={confirmApprove}
      >
        {dialog?.kind === 'approve' && (
          <>
            <TargetSummary rows={dialog.rows} />
            <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs text-zinc-400">
              <p className="mb-1.5 font-semibold text-zinc-200">This will, right away:</p>
              <ul className="list-inside list-disc space-y-1">
                <li>create a Loudrr user account for them</li>
                <li>whitelist it — they get full app access</li>
                <li>carry over their X verification and sign-up score</li>
                <li>credit their referrer, if they used a code</li>
                <li>send them the &ldquo;you&rsquo;re in&rdquo; Telegram message</li>
              </ul>
              <p className="mt-2 text-amber-300/90">
                There is no un-approve in the panel.
              </p>
            </div>
          </>
        )}
      </ActionDialog>

      {/* ---- reject ---- */}
      <ActionDialog
        open={dialog?.kind === 'reject'}
        onClose={closeDialog}
        tone="danger"
        size="lg"
        title={
          dialog?.kind === 'reject' && dialog.rows.length > 1
            ? `Reject ${dialog.rows.length} applicants?`
            : 'Reject this applicant?'
        }
        confirmLabel={
          dialog?.kind === 'reject' && dialog.rows.length > 1
            ? `Reject ${dialog.rows.length}`
            : 'Reject'
        }
        busy={busy}
        error={dialogError}
        blocked={blocked}
        warning="They get a Telegram message right away. The decision is audit-logged, and reversible from the Rejected tab."
        onConfirm={confirmReject}
      >
        {dialog?.kind === 'reject' && (
          <>
            <TargetSummary rows={dialog.rows} />
            <ReasonFields
              idPrefix="wl-reject"
              reason={reason}
              onReasonChange={setReason}
              note={note}
              onNoteChange={setNote}
              presets={REJECT_PRESETS}
              disabled={busy}
              previewFor={(value) =>
                waitlistRejectionPreview(
                  dialog.rows.length === 1 ? dialog.rows[0].x_username : 'handle',
                  value,
                )
              }
            />
          </>
        )}
      </ActionDialog>

      {/* ---- reopen ---- */}
      <ConfirmDialog
        open={dialog?.kind === 'reopen'}
        onClose={closeDialog}
        title="Put this application back in the queue?"
        description="The entry returns to Pending with its rejection reason cleared. Nothing is sent to the applicant."
        confirmLabel="Reopen"
        busy={busy}
        onConfirm={confirmReopen}
        details={
          dialog?.kind === 'reopen' ? (
            <div className="space-y-3">
              <TargetSummary rows={dialog.rows} />
              {dialog.rows[0]?.rejection_reason && (
                <p className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-2.5 text-xs text-zinc-400">
                  Clearing:{' '}
                  <span className="text-zinc-200">
                    &ldquo;{dialog.rows[0].rejection_reason}&rdquo;
                  </span>
                </p>
              )}
              <Input
                label="Internal note (audit log only)"
                value={note}
                maxLength={1000}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. rejected by mistake — wrong row"
              />
              {dialogError && (
                <p role="alert" className="rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-200">
                  {dialogError}
                </p>
              )}
            </div>
          ) : null
        }
      />

      <DetailDrawer row={detail} onClose={() => setDetail(null)} />
    </div>
  );
}

/* ========================================================================== */

function toggleOne(id: string, setSelected: React.Dispatch<React.SetStateAction<string[]>>) {
  setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
}

function emptyTitle(status: WaitlistReviewStatus) {
  if (status === 'submitted') return 'Inbox zero';
  if (status === 'approved') return 'Nobody approved yet';
  return 'No rejections';
}

function emptyDescription(status: WaitlistReviewStatus) {
  if (status === 'submitted') return 'New waitlist signups land here for review.';
  if (status === 'approved') return 'Approved applicants and the user accounts they became.';
  return 'Rejected applications, with the reason each one was given.';
}

function Header({
  status,
  total,
  loading,
  filtersActive,
  onReload,
}: {
  status: WaitlistReviewStatus;
  total: number;
  loading: boolean;
  filtersActive: boolean;
  onReload: () => void;
}) {
  const noun = total === 1 ? 'application' : 'applications';
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-syne text-2xl font-bold tracking-tight">Waitlist</h1>
        <p className="mt-1 text-sm text-zinc-400">
          {num(total)} {filtersActive ? `matching ${noun}` : noun}
          {status === 'submitted' ? ' awaiting review' : ''}
        </p>
      </div>
      {/* Never disabled on a failed load — that was the trap: `rows === null`
          meant both "loading" and "blew up", and it greyed out the only way
          to try again. */}
      <Button variant="secondary" size="sm" onClick={onReload} loading={loading}>
        <RefreshCcw size={12} aria-hidden />
        Refresh
      </Button>
    </div>
  );
}

function Tabs({
  status,
  onChange,
}: {
  status: WaitlistReviewStatus;
  onChange: (next: WaitlistReviewStatus) => void;
}) {
  return (
    <div role="tablist" aria-label="Waitlist status" className="flex gap-1 overflow-x-auto">
      {STATUS_TABS.map((tab) => (
        <button
          key={tab.value}
          role="tab"
          type="button"
          aria-selected={status === tab.value}
          onClick={() => onChange(tab.value)}
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
  );
}

function Filters({
  search,
  onSearch,
  region,
  niche,
  hasScore,
  regions,
  niches,
  onPatch,
  onClear,
  filtersActive,
}: {
  search: string;
  onSearch: (value: string) => void;
  region: string;
  niche: string;
  hasScore: '' | 'yes' | 'no';
  regions: string[];
  niches: string[];
  onPatch: (next: Partial<QueryState>) => void;
  onClear: () => void;
  filtersActive: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-[220px] flex-1">
        <Search
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
        />
        <Input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          className="pl-9"
          placeholder="Search handle, name, Telegram id, referral code…"
          aria-label="Search applicants"
        />
      </div>

      <Select
        value={region}
        onChange={(v) => onPatch({ region: v })}
        aria-label="Filter by region"
      >
        <option value="">All regions</option>
        {regions.map((value) => (
          <option key={value} value={value}>
            {regionLabel(value)}
          </option>
        ))}
      </Select>

      <Select value={niche} onChange={(v) => onPatch({ niche: v })} aria-label="Filter by niche">
        <option value="">All niches</option>
        {niches.map((value) => (
          <option key={value} value={value}>
            {nicheLabel(value)}
          </option>
        ))}
      </Select>

      <Select
        value={hasScore}
        onChange={(v) => onPatch({ hasScore: v as '' | 'yes' | 'no' })}
        aria-label="Filter by score"
      >
        <option value="">Scored or not</option>
        <option value="yes">Has a score</option>
        <option value="no">No score yet</option>
      </Select>

      {filtersActive && (
        <Button variant="ghost" size="sm" onClick={onClear}>
          <X size={12} aria-hidden />
          Clear
        </Button>
      )}
    </div>
  );
}

function Select({
  value,
  onChange,
  children,
  ...rest
}: {
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
} & Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange' | 'children'>) {
  return (
    <select
      {...rest}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-[38px] min-h-[38px] rounded-lg border border-white/[0.08] bg-[#0a0a0a] px-3 text-xs text-zinc-200 focus:border-[#f95400]/40 focus:outline-none"
    >
      {children}
    </select>
  );
}

function SortHeader({
  label,
  active,
  dir,
  onClick,
  className,
}: {
  label: string;
  active: boolean;
  dir: 'asc' | 'desc';
  onClick: () => void;
  className?: string;
}) {
  return (
    <th scope="col" className={cn('px-3 py-3 font-semibold', className)}>
      <button
        type="button"
        onClick={onClick}
        aria-label={`Sort by ${label}, currently ${active ? dir : 'unsorted'}`}
        className={cn(
          'inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-white',
          active ? 'text-white' : 'text-zinc-400',
        )}
      >
        {label}
        <ChevronDown
          size={12}
          aria-hidden
          className={cn(
            'transition-transform',
            !active && 'opacity-30',
            active && dir === 'asc' && 'rotate-180',
          )}
        />
      </button>
    </th>
  );
}

function ErrorState({
  message,
  onRetry,
  hasRows,
}: {
  message: string;
  onRetry: () => void;
  hasRows: boolean;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-red-900/50 bg-red-950/30 p-4"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-400" aria-hidden />
        <div className="text-sm">
          <p className="font-medium text-red-200">Couldn&apos;t load the waitlist</p>
          <p className="mt-0.5 break-words text-xs text-red-300/80">{message}</p>
          {hasRows && (
            <p className="mt-0.5 text-xs text-red-300/60">
              Showing the last rows that loaded — they may be stale.
            </p>
          )}
        </div>
      </div>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        <RotateCcw size={12} aria-hidden />
        Try again
      </Button>
    </div>
  );
}

function BulkBar({
  count,
  onApprove,
  onReject,
  onClear,
}: {
  count: number;
  onApprove: () => void;
  onReject: () => void;
  onClear: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-[#f95400]/25 bg-[#f95400]/[0.07] px-3 py-2">
      <Users size={14} className="text-[#f95400]" aria-hidden />
      <span className="text-sm text-zinc-200">
        {count} selected
      </span>
      <div className="ml-auto flex flex-wrap gap-2">
        <Button size="sm" variant="success" onClick={onApprove}>
          <Check size={12} aria-hidden />
          Approve {count}
        </Button>
        <Button size="sm" variant="danger" onClick={onReject}>
          <X size={12} aria-hidden />
          Reject {count}
        </Button>
        <Button size="sm" variant="ghost" onClick={onClear}>
          Clear
        </Button>
      </div>
    </div>
  );
}

/** "12.4k followers · 2y old · referred by @x" */
function SubLine({ row }: { row: WaitlistReviewRow }) {
  const bits: string[] = [];
  if (typeof row.followers_count === 'number') bits.push(`${compact(row.followers_count)} followers`);
  const age = accountAge(row.register_date);
  if (age) bits.push(age);
  if (row.referrer_handle) bits.push(`referred by @${row.referrer_handle}`);
  else if (row.referral_code_used) bits.push(`code ${row.referral_code_used}`);
  if (!bits.length) return null;
  return <p className="mt-0.5 truncate text-[11px] text-zinc-500">{bits.join(' · ')}</p>;
}

function RingFlag({ row }: { row: WaitlistReviewRow }) {
  if (row.referral_code_uses < 3) return null;
  return (
    <Badge
      tone="warning"
      className="normal-case"
      // 5 of the seeded pending entries came in on one code
    >
      <span title={`${row.referral_code_uses} applications used ${row.referral_code_used}`}>
        ring ×{row.referral_code_uses}
      </span>
    </Badge>
  );
}

function ScoreCell({
  row,
  onRefresh,
  refreshing,
  canAct,
}: {
  row: WaitlistReviewRow;
  onRefresh: () => void;
  refreshing: boolean;
  canAct: boolean;
}) {
  if (row.score !== null) {
    return (
      <div className="whitespace-nowrap" title={`Fetched ${absolute(row.score_updated_at)}`}>
        <div className="font-mono tabular-nums text-zinc-100">{num(Math.round(row.score))}</div>
        <div className="text-[11px] text-zinc-400">
          {row.tier}
          {typeof row.smart_followers === 'number' && row.smart_followers > 0 &&
            ` · ${num(row.smart_followers)} smart`}
        </div>
      </div>
    );
  }

  // Two different nulls, and the panel used to show one word for both with no
  // way to act on either.
  const neverFetched = row.score_updated_at === null;
  return (
    <div className="flex items-center justify-end gap-1.5 whitespace-nowrap">
      <span
        className="text-[11px] text-zinc-500"
        title={
          neverFetched
            ? 'Pending — the sign-up score fetch has not completed for this applicant yet.'
            : `No score — the provider was asked ${timeAgo(row.score_updated_at)} and had nothing for this handle.`
        }
      >
        {neverFetched ? 'pending' : 'no score'}
      </span>
      {canAct && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label={`Re-fetch the score for ${row.x_username || identityLabel(row)}`}
          title="Re-fetch this applicant's score"
          className="rounded-md p-1 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-white disabled:opacity-40"
        >
          <RefreshCcw size={12} aria-hidden className={cn(refreshing && 'animate-spin')} />
        </button>
      )}
    </div>
  );
}

function HandleLink({ handle, className }: { handle: string; className?: string }) {
  const bare = bareHandle(handle);
  if (!bare) return <span className="text-zinc-600">{EM_DASH}</span>;
  return (
    <button
      type="button"
      // Telegram's in-app WebView swallows target=_blank; openLink hands the
      // URL to the Telegram API when we're inside it, window.open otherwise.
      onClick={() => openLink(`https://x.com/${bare}`)}
      className={cn('text-white transition-colors hover:text-[#f95400] hover:underline', className)}
    >
      @{bare}
    </button>
  );
}

interface RowActionsProps {
  row: WaitlistReviewRow;
  canAct: boolean;
  onApprove: () => void;
  onReject: () => void;
  onReopen: () => void;
  onDetail: () => void;
}

const reopenTitle = (row: WaitlistReviewRow) =>
  row.created_user_id
    ? 'This entry already created a user account — it can’t be reopened'
    : 'Return this application to the pending queue';

/** Compact row of actions for the desktop table. */
function TableActions({ row, canAct, onApprove, onReject, onReopen, onDetail }: RowActionsProps) {
  return (
    <div className="flex justify-end gap-1.5">
      {/* Icon-only here: the label costs ~70px of a column that already has
          to fit two decisions at 1280. The card list below md spells it out. */}
      <Button
        size="sm"
        variant="ghost"
        onClick={onDetail}
        aria-label={`Open details for ${identityLabel(row)}`}
        title="Everything on this application"
        className="px-2"
      >
        <Info size={14} aria-hidden />
      </Button>
      {canAct && (
        <>
          <Button size="sm" variant="success" onClick={onApprove}>
            <Check size={12} aria-hidden />
            Approve
          </Button>
          <Button size="sm" variant="danger" onClick={onReject}>
            <X size={12} aria-hidden />
            Reject
          </Button>
        </>
      )}
      {row.status === 'rejected' && (
        <Button
          size="sm"
          variant="secondary"
          onClick={onReopen}
          disabled={Boolean(row.created_user_id)}
          title={reopenTitle(row)}
        >
          <Undo2 size={12} aria-hidden />
          Reopen
        </Button>
      )}
    </div>
  );
}

/**
 * Card actions: the two decisions side by side, everything else under them.
 * Every target is at least 44px tall — this is the layout that exists because
 * the table's action column was physically unreachable on a phone.
 */
function CardActions({
  row,
  canAct,
  onApprove,
  onReject,
  onReopen,
  onDetail,
  onRefreshScore,
  refreshing,
}: RowActionsProps & { onRefreshScore: () => void; refreshing: boolean }) {
  return (
    <div className="mt-3 space-y-2 [&_button]:min-h-[44px] [&_button]:w-full">
      {canAct && (
        <div className="grid grid-cols-2 gap-2">
          <Button size="sm" variant="success" onClick={onApprove}>
            <Check size={14} aria-hidden />
            Approve
          </Button>
          <Button size="sm" variant="danger" onClick={onReject}>
            <X size={14} aria-hidden />
            Reject
          </Button>
        </div>
      )}
      <div className={cn('grid gap-2', row.status === 'rejected' ? 'grid-cols-2' : 'grid-cols-1')}>
        <Button size="sm" variant="secondary" onClick={onDetail}>
          <Info size={14} aria-hidden />
          Details
        </Button>
        {row.status === 'rejected' && (
          <Button
            size="sm"
            variant="secondary"
            onClick={onReopen}
            disabled={Boolean(row.created_user_id)}
            title={reopenTitle(row)}
          >
            <Undo2 size={14} aria-hidden />
            Reopen
          </Button>
        )}
      </div>
      {canAct && row.score === null && (
        <Button size="sm" variant="ghost" onClick={onRefreshScore} loading={refreshing}>
          <RefreshCcw size={14} aria-hidden />
          Re-fetch score
        </Button>
      )}
    </div>
  );
}

function Row({
  row,
  canAct,
  status,
  selected,
  onToggle,
  onApprove,
  onReject,
  onReopen,
  onDetail,
  onRefreshScore,
  refreshing,
}: {
  row: WaitlistReviewRow;
  canAct: boolean;
  status: WaitlistReviewStatus;
  selected: boolean;
  onToggle: () => void;
  onRefreshScore: () => void;
  refreshing: boolean;
} & Omit<RowActionsProps, 'row' | 'canAct'>) {
  return (
    <tr className={cn('bg-[#111] transition-colors hover:bg-[#161616]', selected && 'bg-[#17120f]')}>
      {canAct && (
        <td className="px-3 py-3">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            aria-label={`Select ${identityLabel(row)}`}
            className="h-4 w-4 accent-[#f95400]"
          />
        </td>
      )}
      <td className="max-w-[200px] px-3 py-3">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-white">{identityLabel(row)}</span>
          <RingFlag row={row} />
        </div>
        <SubLine row={row} />
      </td>
      <td className="px-3 py-3">
        <span className="inline-flex items-center gap-1.5">
          <HandleLink handle={row.x_username} />
          {row.x_verified ? (
            <Badge tone="success">
              <span title="Handle proved via X OAuth at registration">oauth</span>
            </Badge>
          ) : (
            <Badge tone="neutral">
              <span title="Legacy entry — the handle was pasted, not OAuth-verified">unverified</span>
            </Badge>
          )}
        </span>
      </td>
      <td className="px-3 py-3 text-right">
        <ScoreCell row={row} onRefresh={onRefreshScore} refreshing={refreshing} canAct={canAct} />
      </td>
      <td className="max-w-[140px] px-3 py-3 text-xs">
        {/* stacked, not "Latin America / Memecoins" on one line — that single
            cell was pushing the action column past the viewport edge */}
        <div className="truncate text-zinc-300">
          {regionLabel(row.region) || <span className="text-zinc-600">{EM_DASH}</span>}
        </div>
        <div className="truncate text-[11px] text-zinc-500">
          {nicheLabel(row.niche) || EM_DASH}
        </div>
      </td>
      <td className="px-3 py-3 text-xs">
        {row.referral_code_used ? (
          <div>
            <span className="font-mono text-zinc-300">{row.referral_code_used}</span>
            <div className="text-[11px] text-zinc-500">
              {row.referrer_handle ? `@${row.referrer_handle}` : 'unknown owner'}
            </div>
          </div>
        ) : (
          <span className="text-zinc-600">{EM_DASH}</span>
        )}
      </td>
      <td className="px-3 py-3 text-xs text-zinc-400">
        <span title={absolute(status === 'submitted' ? row.created_at : row.decided_at, { seconds: true })}>
          {timeAgo(status === 'submitted' ? row.created_at : row.decided_at)}
        </span>
        {status === 'rejected' && row.rejection_reason && (
          <p className="mt-0.5 max-w-[200px] truncate text-[11px] text-zinc-500" title={row.rejection_reason}>
            &ldquo;{row.rejection_reason}&rdquo;
          </p>
        )}
      </td>
      <td className="px-3 py-3">
        <TableActions
          row={row}
          canAct={canAct}
          onApprove={onApprove}
          onReject={onReject}
          onReopen={onReopen}
          onDetail={onDetail}
        />
      </td>
    </tr>
  );
}

function RowCard({
  row,
  canAct,
  selected,
  onToggle,
  onApprove,
  onReject,
  onReopen,
  onDetail,
  onRefreshScore,
  refreshing,
}: {
  row: WaitlistReviewRow;
  canAct: boolean;
  selected: boolean;
  onToggle: () => void;
  onRefreshScore: () => void;
  refreshing: boolean;
} & Omit<RowActionsProps, 'row' | 'canAct'>) {
  return (
    <li
      className={cn(
        'rounded-2xl border border-white/[0.06] bg-[#111] p-4',
        selected && 'border-[#f95400]/40 bg-[#17120f]',
      )}
    >
      <div className="flex items-start gap-3">
        {canAct && (
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            aria-label={`Select ${identityLabel(row)}`}
            className="mt-1 h-5 w-5 shrink-0 accent-[#f95400]"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate font-medium text-white">{identityLabel(row)}</span>
            <RingFlag row={row} />
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-sm">
            <HandleLink handle={row.x_username} className="text-zinc-300" />
            {row.x_verified && <Badge tone="success">oauth</Badge>}
          </div>
          <SubLine row={row} />
        </div>
        <div className="shrink-0 text-right">
          {/* canAct=false: the tiny inline icon is a 20px touch target. On a
              card the re-fetch is a full-width button below instead. */}
          <ScoreCell row={row} onRefresh={onRefreshScore} refreshing={refreshing} canAct={false} />
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs">
        <dt className="text-zinc-500">Region</dt>
        <dd className="text-right text-zinc-300">{regionLabel(row.region) || EM_DASH}</dd>
        <dt className="text-zinc-500">Niche</dt>
        <dd className="text-right text-zinc-300">{nicheLabel(row.niche) || EM_DASH}</dd>
        <dt className="text-zinc-500">{row.status === 'submitted' ? 'Submitted' : 'Decided'}</dt>
        <dd className="text-right text-zinc-300">
          {timeAgo(row.status === 'submitted' ? row.created_at : row.decided_at)}
        </dd>
      </dl>

      {row.status === 'rejected' && row.rejection_reason && (
        <p className="mt-2 rounded-lg bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-zinc-400">
          &ldquo;{row.rejection_reason}&rdquo;
        </p>
      )}

      <CardActions
        row={row}
        canAct={canAct}
        onApprove={onApprove}
        onReject={onReject}
        onReopen={onReopen}
        onDetail={onDetail}
        onRefreshScore={onRefreshScore}
        refreshing={refreshing}
      />
    </li>
  );
}

function TargetSummary({ rows }: { rows: WaitlistReviewRow[] }) {
  if (rows.length === 0) return null;
  if (rows.length > 1) {
    return (
      <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
        <p className="mb-1.5 font-semibold text-zinc-200">{rows.length} applicants</p>
        <ul className="max-h-40 space-y-0.5 overflow-y-auto text-zinc-400">
          {rows.map((row) => (
            <li key={row.id} className="truncate">
              {identityLabel(row)}
              {row.x_username && <span className="text-zinc-600"> · @{bareHandle(row.x_username)}</span>}
              {row.score !== null && <span className="text-zinc-600"> · {num(Math.round(row.score))}</span>}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const row = rows[0];
  return (
    <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
      <dl className="grid grid-cols-[92px_1fr] gap-y-1.5">
        <dt className="text-zinc-500">Applicant</dt>
        <dd className="text-zinc-100">{identityLabel(row)}</dd>
        <dt className="text-zinc-500">X handle</dt>
        <dd className="text-zinc-100">
          {row.x_username ? `@${bareHandle(row.x_username)}` : EM_DASH}
          {typeof row.followers_count === 'number' && (
            <span className="text-zinc-500"> · {compact(row.followers_count)} followers</span>
          )}
        </dd>
        <dt className="text-zinc-500">Score</dt>
        <dd className="text-zinc-100">
          {row.score === null
            ? row.score_updated_at
              ? 'no score'
              : 'pending'
            : `${num(Math.round(row.score))} · ${row.tier}`}
        </dd>
        <dt className="text-zinc-500">Region / Niche</dt>
        <dd className="text-zinc-100">
          {regionLabel(row.region) || EM_DASH} / {nicheLabel(row.niche) || EM_DASH}
        </dd>
        <dt className="text-zinc-500">Referral</dt>
        <dd className="text-zinc-100">
          {row.referral_code_used ? (
            <>
              {row.referral_code_used}
              {row.referrer_handle && <span className="text-zinc-500"> · @{row.referrer_handle}</span>}
              {row.referral_code_uses >= 3 && (
                <span className="text-amber-400"> · {row.referral_code_uses} on this code</span>
              )}
            </>
          ) : (
            EM_DASH
          )}
        </dd>
      </dl>
    </div>
  );
}

function DetailDrawer({ row, onClose }: { row: WaitlistReviewRow | null; onClose: () => void }) {
  return (
    <Modal
      open={Boolean(row)}
      onClose={onClose}
      size="lg"
      title={row ? identityLabel(row) : 'Applicant'}
      description={row?.x_username ? `@${bareHandle(row.x_username)}` : undefined}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      {row && (
        <div className="space-y-4 text-sm">
          {row.bio && (
            <p className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs italic text-zinc-300">
              &ldquo;{row.bio}&rdquo;
            </p>
          )}

          <Section title="X account">
            <Fact label="Handle" value={row.x_username ? `@${bareHandle(row.x_username)}` : EM_DASH} />
            <Fact
              label="Numeric id"
              value={row.x_user_id || EM_DASH}
              copy={row.x_user_id || undefined}
              mono
            />
            <Fact label="Profile name" value={row.profile_name || EM_DASH} />
            <Fact label="Followers" value={row.followers_count === null ? EM_DASH : num(row.followers_count)} />
            <Fact label="Following" value={row.following_count === null ? EM_DASH : num(row.following_count)} />
            <Fact label="Posts" value={row.tweets_count === null ? EM_DASH : num(row.tweets_count)} />
            <Fact label="Smart followers" value={row.smart_followers === null ? EM_DASH : num(row.smart_followers)} />
            <Fact
              label="Account created"
              value={row.register_date ? `${row.register_date} (${accountAge(row.register_date)})` : EM_DASH}
            />
            <Fact label="Category" value={row.category || EM_DASH} />
          </Section>

          <Section title="Telegram">
            <Fact label="Username" value={row.telegram_username ? `@${row.telegram_username}` : EM_DASH} />
            <Fact label="Display name" value={row.telegram_display_name || EM_DASH} />
            <Fact
              label="Telegram id"
              value={row.telegram_id === null ? EM_DASH : String(row.telegram_id)}
              copy={row.telegram_id === null ? undefined : String(row.telegram_id)}
              mono
            />
          </Section>

          <Section title="Referral">
            <Fact label="Their own code" value={row.referral_code || EM_DASH} copy={row.referral_code || undefined} mono />
            <Fact label="Applications they brought" value={num(row.total_referrals)} />
            <Fact label="Code they used" value={row.referral_code_used || EM_DASH} mono />
            <Fact label="Referrer" value={row.referrer_handle ? `@${row.referrer_handle}` : EM_DASH} />
            <Fact
              label="Applications on that code"
              value={row.referral_code_used ? num(row.referral_code_uses) : EM_DASH}
              tone={row.referral_code_uses >= 3 ? 'warn' : undefined}
            />
          </Section>

          {row.other_platforms.length > 0 && (
            <Section title="Other platforms">
              {row.other_platforms.map((p, i) => (
                <Fact
                  key={`${p.platform}-${i}`}
                  label={p.platform_name || p.platform || 'platform'}
                  value={p.username || EM_DASH}
                />
              ))}
            </Section>
          )}

          <Section title="Application">
            <Fact label="Status" value={row.status} />
            <Fact label="Submitted" value={absolute(row.created_at, { seconds: true })} />
            <Fact
              label="X OAuth at signup"
              value={row.x_verified ? 'yes' : 'no (legacy paste-in entry)'}
            />
            {row.x_verified_previously && <Fact label="Verified before a prior rejection" value="yes" />}
            {row.decided_at && <Fact label="Decided" value={absolute(row.decided_at, { seconds: true })} />}
            {row.decided_by_handle && <Fact label="Decided by" value={`@${row.decided_by_handle}`} />}
            {row.status === 'rejected' && (
              <Fact label="Reason they were given" value={row.rejection_reason || '(none)'} />
            )}
            {row.created_user_id && (
              <Fact label="User account" value={row.created_user_id} copy={row.created_user_id} mono />
            )}
          </Section>
        </div>
      )}
    </Modal>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      <dl className="grid grid-cols-[minmax(0,170px)_1fr] gap-y-1 rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
        {children}
      </dl>
    </div>
  );
}

function Fact({
  label,
  value,
  copy,
  mono,
  tone,
}: {
  label: string;
  value: string;
  copy?: string;
  mono?: boolean;
  tone?: 'warn';
}) {
  return (
    <>
      <dt className="pr-3 text-zinc-500">{label}</dt>
      <dd
        className={cn(
          'flex min-w-0 items-center gap-2 break-words',
          mono && 'font-mono',
          tone === 'warn' ? 'text-amber-300' : 'text-zinc-200',
        )}
      >
        <span className="min-w-0 break-all">{value}</span>
        {copy && <CopyButton value={copy} className="h-6" />}
      </dd>
    </>
  );
}
