'use client';

import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Megaphone, Pause, Play, Plus, RefreshCcw, Trash2 } from 'lucide-react';
import { adminApi, SponsorRow } from '@/lib/api';
import { Button } from '@/components/admin/Button';
import { Modal } from '@/components/admin/Modal';
import { Input, Textarea } from '@/components/admin/Input';
import { Badge } from '@/components/admin/Badge';
import { EmptyState } from '@/components/admin/EmptyState';
import { InlineEdit, type InlineEditValue } from '@/components/admin/InlineEdit';
import { TableSkeleton } from '@/components/admin/Skeleton';
import { cn } from '@/lib/utils';

const DEFAULT_KARMA = 100;
const MAX_KARMA = 100_000;
const KARMA_RULE = 'Whole number, 1 – 100,000';

function isValidKarma(n: number): boolean {
  return Number.isInteger(n) && n >= 1 && n <= MAX_KARMA;
}

// adminApiRequest throws "<status>: <detail>"; the detail is what a person needs.
function errorText(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  return msg.replace(/^\d{3}: /, '');
}

// A 5xx, or a network error (no "<status>: " prefix), can be the proxy giving
// up while the backend still applied the change — reload to show what's real.
function mayHaveApplied(e: unknown): boolean {
  const status = e instanceof Error ? /^(\d{3}): /.exec(e.message)?.[1] : undefined;
  return status === undefined || Number(status) >= 500;
}

// The backend sends naive UTC ("2026-09-17T12:00:00.123456"). Without a zone
// suffix Date reads it as local time, so pin it to UTC (and trim microseconds).
function serverTime(iso: string): number {
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`;
  return new Date(zoned.replace(/(\.\d{3})\d+/, '$1')).getTime();
}

function timeAgo(iso: string | null, now: number): string {
  if (!iso) return 'never';
  const then = serverTime(iso);
  if (Number.isNaN(then)) return '—';
  const min = Math.floor(Math.max(0, now - then) / 60_000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(then).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtNumber(n: number): string {
  return n.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function SponsorAvatar({ sponsor }: { sponsor: SponsorRow }) {
  const [failed, setFailed] = useState(false);
  if (sponsor.avatar_url && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- avatar hosts aren't guaranteed to be in images.remotePatterns
      <img
        src={sponsor.avatar_url}
        alt=""
        onError={() => setFailed(true)}
        className="h-9 w-9 shrink-0 rounded-full bg-white/[0.04] object-cover"
      />
    );
  }
  return (
    <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#f95400]/15 text-sm font-bold text-[#f95400]">
      {(sponsor.display_name || sponsor.x_username || '?').charAt(0).toUpperCase()}
    </div>
  );
}

export default function AdminSponsorsPage() {
  const [rows, setRows] = useState<SponsorRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // "now" for the relative times; set on load and ticked so "3m ago" stays true
  const [now, setNow] = useState(0);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<SponsorRow | null>(null);
  const [removing, setRemoving] = useState(false);

  const [handle, setHandle] = useState('');
  const [karma, setKarma] = useState(String(DEFAULT_KARMA));
  const [notes, setNotes] = useState('');
  const [adding, setAdding] = useState(false);

  const karmaValid = isValidKarma(Number(karma));

  // resolves to the fresh rows, or null when the load failed
  async function load(): Promise<SponsorRow[] | null> {
    setLoading(true);
    setError(null);
    try {
      const list = await adminApi.listSponsors();
      setRows(list);
      setNow(Date.now());
      return list;
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      toast.error('Failed to load sponsors', { description: msg });
      return null;
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  // PATCH returns the full row, stats included
  function mergeRow(updated: SponsorRow) {
    setRows((prev) => (prev ? prev.map((r) => (r.id === updated.id ? updated : r)) : prev));
  }

  async function addSponsor(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = handle.trim();
    if (!trimmed || !karmaValid || adding) return;
    setAdding(true);
    try {
      const row = await adminApi.addSponsor(trimmed, Number(karma), notes.trim());
      // a renamed account comes back as its existing row (same id, new handle)
      const previous = rows?.find((r) => r.id === row.id);
      if (rows === null) {
        await load();
      } else {
        setRows((prev) =>
          prev?.some((r) => r.id === row.id)
            ? prev.map((r) => (r.id === row.id ? row : r))
            : [row, ...(prev ?? [])],
        );
      }
      setHandle('');
      setKarma(String(DEFAULT_KARMA));
      setNotes('');
      if (previous && previous.x_username !== row.x_username) {
        toast.success(`Updated @${previous.x_username} → @${row.x_username}`, {
          description: `Same X account under a new handle. Its stats carry over; karma per post is ${fmtNumber(row.karma_per_post)}.`,
        });
      } else {
        toast.success(`Now monitoring @${row.x_username}`, {
          description: `Its new posts become sponsored raids worth ${fmtNumber(row.karma_per_post)} karma.`,
        });
      }
    } catch (err) {
      toast.error("Couldn't add account", { description: errorText(err) });
      if (mayHaveApplied(err)) load();
    } finally {
      setAdding(false);
    }
  }

  async function toggleActive(s: SponsorRow) {
    const resume = !s.is_active;
    setTogglingId(s.id);
    try {
      mergeRow(await adminApi.updateSponsor(s.id, { is_active: resume }));
      if (resume) {
        toast.success(`Resumed @${s.x_username}`, {
          description: "New posts become sponsored raids again. Posts made while paused aren't imported.",
        });
      } else {
        toast.success(`Paused @${s.x_username}`, {
          description: 'New posts are ignored until you resume. Live sponsored posts stay up.',
        });
      }
    } catch (e) {
      toast.error(resume ? "Couldn't resume" : "Couldn't pause", { description: errorText(e) });
      if (mayHaveApplied(e)) load();
    } finally {
      setTogglingId(null);
    }
  }

  // InlineEdit shows the thrown message as a toast and reverts the field.
  async function saveKarma(s: SponsorRow, value: InlineEditValue) {
    const n = Number(value);
    if (!isValidKarma(n)) {
      throw new Error(`Karma per post must be a whole number from 1 to ${MAX_KARMA.toLocaleString('en-US')}`);
    }
    try {
      mergeRow(await adminApi.updateSponsor(s.id, { karma_per_post: n }));
    } catch (e) {
      // the rows land after InlineEdit has toasted this error and reverted
      if (mayHaveApplied(e)) load();
      throw new Error(errorText(e));
    }
    toast.success(`@${s.x_username} now pays ${fmtNumber(n)} karma per post`, {
      description: 'Applies to new posts. Live ones keep their current karma.',
    });
  }

  async function confirmRemove() {
    if (!removeTarget) return;
    const s = removeTarget;
    setRemoving(true);
    try {
      await adminApi.deleteSponsor(s.id);
      setRows((prev) => (prev ? prev.filter((r) => r.id !== s.id) : prev));
      setRemoveTarget(null);
      toast.success(`Removed @${s.x_username}`, {
        description: 'Its live sponsored posts stay up until they expire.',
      });
    } catch (e) {
      toast.error("Couldn't remove account", { description: errorText(e) });
      if (mayHaveApplied(e)) {
        const fresh = await load();
        // it did go through: nothing left to confirm
        if (fresh && !fresh.some((r) => r.id === s.id)) setRemoveTarget(null);
      }
    } finally {
      setRemoving(false);
    }
  }

  const activeCount = rows ? rows.filter((r) => r.is_active).length : 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-syne text-2xl font-bold tracking-tight">Sponsors</h1>
          <p className="mt-1 max-w-2xl text-sm text-zinc-500">
            New posts from these X accounts — not replies or retweets — automatically become sponsored raids,
            shown first in Engage.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={load} disabled={loading}>
          <RefreshCcw size={12} className={cn(loading && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      <form onSubmit={addSponsor} className="rounded-2xl border border-white/[0.06] bg-[#111] p-5">
        <h2 className="text-sm font-semibold text-white">Add an account</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_11rem]">
          <Input
            id="sponsor-handle"
            label="X handle"
            placeholder="@handle or x.com/handle"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            disabled={adding}
            maxLength={120}
            autoComplete="off"
            spellCheck={false}
          />
          <Input
            id="sponsor-karma"
            label="Karma per post"
            type="number"
            inputMode="numeric"
            min={1}
            max={MAX_KARMA}
            step={1}
            value={karma}
            onChange={(e) => setKarma(e.target.value)}
            disabled={adding}
            hint={KARMA_RULE}
            error={karma !== '' && !karmaValid ? KARMA_RULE : undefined}
          />
        </div>
        <div className="mt-3">
          <Textarea
            id="sponsor-notes"
            label="Notes (optional)"
            placeholder="e.g. partner campaign, runs through October"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={adding}
            maxLength={2000}
            rows={2}
            className="min-h-[60px]"
          />
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-zinc-600">Only posts made after you add the account are imported.</p>
          <Button type="submit" loading={adding} disabled={!handle.trim() || !karmaValid}>
            {!adding && <Plus size={14} />}
            Add account
          </Button>
        </div>
      </form>

      {error && (
        <div className="rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">{error}</div>
      )}

      {rows === null ? (
        loading && <TableSkeleton rows={4} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Megaphone}
          title="No sponsored accounts yet"
          description="Add an X handle above. Its next original post will show up first in Engage."
        />
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-zinc-500">
            {rows.length} account{rows.length === 1 ? '' : 's'} · {activeCount} active
          </p>
          <div className="overflow-x-auto rounded-2xl border border-white/[0.06]">
            <table className="w-full text-sm">
              <thead className="bg-[#0d0d0d] text-left text-[11px] uppercase tracking-wide text-zinc-500">
                <tr className="whitespace-nowrap">
                  <th className="px-4 py-3 font-semibold">Account</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Karma / post</th>
                  <th className="px-4 py-3 text-right font-semibold">Posts</th>
                  <th className="px-4 py-3 text-right font-semibold">Live</th>
                  <th className="px-4 py-3 text-right font-semibold">Engagements</th>
                  <th className="px-4 py-3 text-right font-semibold">Karma paid</th>
                  <th className="px-4 py-3 font-semibold">Last post</th>
                  <th className="px-4 py-3 font-semibold">Notes</th>
                  <th className="px-4 py-3 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {rows.map((s) => (
                  <tr key={s.id} className="bg-[#111] transition-colors hover:bg-[#161616]">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <SponsorAvatar key={s.avatar_url} sponsor={s} />
                        <div className="min-w-0">
                          <div className="max-w-[12rem] truncate text-white">
                            {s.display_name || `@${s.x_username}`}
                          </div>
                          <a
                            href={`https://x.com/${s.x_username}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="whitespace-nowrap text-xs text-zinc-400 hover:text-[#f95400] hover:underline"
                          >
                            @{s.x_username}
                          </a>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={s.is_active ? 'success' : 'neutral'}>{s.is_active ? 'Active' : 'Paused'}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      <InlineEdit kind="number" value={s.karma_per_post} onSave={(v) => saveKarma(s, v)} />
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                      {fmtNumber(s.posts_created)}
                    </td>
                    <td
                      className={cn(
                        'px-4 py-3 text-right font-mono tabular-nums',
                        s.active_posts > 0 ? 'text-[#f95400]' : 'text-zinc-500',
                      )}
                    >
                      {fmtNumber(s.active_posts)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                      {fmtNumber(s.engagements)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                      {fmtNumber(s.karma_paid)}
                    </td>
                    <td
                      className="whitespace-nowrap px-4 py-3 text-xs text-zinc-400"
                      title={s.last_post_at ? new Date(serverTime(s.last_post_at)).toLocaleString() : undefined}
                    >
                      {s.last_post_at && s.last_tweet_id ? (
                        <a
                          href={`https://x.com/${s.x_username}/status/${s.last_tweet_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="hover:text-[#f95400] hover:underline"
                        >
                          {timeAgo(s.last_post_at, now)}
                        </a>
                      ) : (
                        <span className={cn(!s.last_post_at && 'text-zinc-600')}>{timeAgo(s.last_post_at, now)}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-400">
                      {s.notes ? (
                        <div className="max-w-[14rem] truncate" title={s.notes}>{s.notes}</div>
                      ) : (
                        <span className="text-zinc-700">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex gap-1.5">
                        {s.is_active ? (
                          <Button
                            size="sm"
                            variant="secondary"
                            loading={togglingId === s.id}
                            onClick={() => toggleActive(s)}
                          >
                            {togglingId !== s.id && <Pause size={12} />}
                            Pause
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="success"
                            loading={togglingId === s.id}
                            onClick={() => toggleActive(s)}
                          >
                            {togglingId !== s.id && <Play size={12} />}
                            Resume
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={togglingId === s.id}
                          onClick={() => setRemoveTarget(s)}
                        >
                          <Trash2 size={12} />
                          Remove
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Modal
        open={!!removeTarget}
        onClose={() => { if (!removing) setRemoveTarget(null); }}
        title={`Remove @${removeTarget?.x_username ?? ''}?`}
        description="Its new posts stop becoming sponsored raids. Sponsored posts it already has stay live in Engage until they expire."
        footer={
          <>
            <Button variant="secondary" onClick={() => setRemoveTarget(null)} disabled={removing}>
              Cancel
            </Button>
            <Button variant="danger" loading={removing} onClick={confirmRemove}>
              Remove
            </Button>
          </>
        }
      >
        {removeTarget && (
          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
              <div className="grid grid-cols-[110px_1fr] gap-y-1.5">
                <span className="text-zinc-500">Posts created</span>
                <span className="font-mono text-zinc-200">{fmtNumber(removeTarget.posts_created)}</span>
                <span className="text-zinc-500">Live now</span>
                <span className="font-mono text-zinc-200">{fmtNumber(removeTarget.active_posts)}</span>
                <span className="text-zinc-500">Karma paid</span>
                <span className="font-mono text-zinc-200">{fmtNumber(removeTarget.karma_paid)}</span>
              </div>
            </div>
            <p className="text-xs text-zinc-500">To stop for now and keep these stats here, pause it instead.</p>
          </div>
        )}
      </Modal>
    </div>
  );
}
