'use client';

/**
 * Site Settings — the karma economy's control panel.
 *
 * Kill switches are pinned at the top, because in an incident nobody should
 * be hunting for them in a left-hand nav. Everything else keeps the
 * group nav / card list layout.
 *
 * On propagation (the old "takes effect immediately" claim): settings are
 * cached per PROCESS for 300s, and the arq settlement worker is a different
 * process from the API. Rather than add a Redis invalidation channel days
 * before launch, the copy here is honest — a saved value applies to this API
 * process now and reaches the settlement worker within five minutes. The one
 * exception is the kill switches, which the backend reads fresh on every
 * check precisely so an emergency stop is never five minutes late.
 */

import { useCallback, useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  AlertTriangle,
  Coins,
  Crown,
  Flame,
  Info,
  Megaphone,
  MessageSquare,
  Power,
  RefreshCcw,
  Search,
  Settings2,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';

import { adminApi, type SiteSettingRow, type SiteSettingsGroup } from '@/lib/api';
import { Badge } from '@/components/admin/Badge';
import { Button } from '@/components/admin/Button';
import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { EmptyState } from '@/components/admin/EmptyState';
import { Skeleton } from '@/components/admin/Skeleton';
import { useAdminSession } from '@/lib/admin/session';
import { useAdminResource } from '@/lib/admin/useAdminResource';
import { cn } from '@/lib/utils';
import { BoolToggle, SettingCard, type SaveResult } from './SettingCard';

const KILL_SWITCH_GROUP = 'Kill switches';

const GROUP_ICONS: Record<string, LucideIcon> = {
  [KILL_SWITCH_GROUP]: Power,
  Economy: Coins,
  'Verification & anti-gaming': ShieldCheck,
  'Tier thresholds (TweetScout score)': Crown,
  'Tier multipliers': TrendingUp,
  Streaks: Flame,
  'Sponsored posts': Megaphone,
  Scores: TrendingUp,
  'Karma decay': TrendingDown,
  'Telegram message templates': MessageSquare,
};

function iconForGroup(name: string): LucideIcon {
  return GROUP_ICONS[name] ?? Settings2;
}

export default function SiteSettingsPage() {
  const { isSuperadmin } = useAdminSession();
  const [query, setQuery] = useState('');
  const [liveOnly, setLiveOnly] = useState(false);
  const [activeGroup, setActiveGroup] = useState<string | null>(null);

  const { data, loading, error, reload } = useAdminResource(
    () => adminApi.getSiteSettings(), [],
  );

  const groups = useMemo(
    () => (data?.groups ?? []).filter((g) => g.name !== KILL_SWITCH_GROUP),
    [data],
  );
  const killSwitches = useMemo(
    () => data?.groups.find((g) => g.name === KILL_SWITCH_GROUP) ?? null,
    [data],
  );

  /**
   * Saving reports EXACTLY ONCE. The previous version toasted the failure and
   * then threw a `SilentSaveError` carrying an empty message, which the inline
   * editor toasted again — that was the second, blank toast.
   */
  const save = useCallback<(s: SiteSettingRow, value: string) => Promise<SaveResult>>(
    async (setting, value) => {
      try {
        await adminApi.updateSiteSetting(setting.key, value);
        toast.success(`Saved ${setting.key}`, {
          description: setting.live
            ? 'Applies to this API process now; the settlement worker picks it up within 5 minutes.'
            : 'Stored. No backend code reads this setting yet.',
        });
        await reload();
        return { ok: true };
      } catch (e) {
        const raw = e instanceof Error ? e.message : 'Save failed';
        const detail = raw.replace(/^\d+:\s*/, '');
        const message = raw.startsWith('403')
          ? 'Superadmin required to change settings.'
          : detail;
        toast.error(`Couldn't save ${setting.key}`, { description: message });
        return { ok: false, message };
      }
    },
    [reload],
  );

  const q = query.trim().toLowerCase();
  const isFlatView = q.length > 0 || liveOnly;

  const matches = useCallback(
    (s: SiteSettingRow) => {
      if (liveOnly && !s.live) return false;
      if (!q) return true;
      return s.key.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);
    },
    [q, liveOnly],
  );

  const flatGroups = useMemo<SiteSettingsGroup[]>(
    () => groups
      .map((g) => ({ ...g, settings: g.settings.filter(matches) }))
      .filter((g) => g.settings.length > 0),
    [groups, matches],
  );

  const currentGroupName = activeGroup ?? groups[0]?.name ?? null;
  const selectedGroup = useMemo<SiteSettingsGroup | null>(() => {
    const g = groups.find((x) => x.name === currentGroupName);
    if (!g) return null;
    return { ...g, settings: g.settings.filter((s) => (liveOnly ? s.live : true)) };
  }, [groups, currentGroupName, liveOnly]);

  const driftedCount = useMemo(
    () => (data?.groups ?? []).reduce(
      (acc, g) => acc + g.settings.filter((s) => s.drifted).length, 0,
    ),
    [data],
  );

  if (error && !data) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="Couldn't load site settings"
        description={
          error.startsWith('403')
            ? "You're authenticated but lack the admin role. Bootstrap via backend/scripts/seed_admins.py."
            : error
        }
        action={
          <Button variant="secondary" size="sm" onClick={reload}>
            <RefreshCcw size={12} aria-hidden />
            Retry
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-syne text-2xl font-bold tracking-tight">Site Settings</h1>
          <p className="mt-1 max-w-2xl text-sm text-zinc-500">
            Tune the karma economy, tiers and verification policy. Editing requires
            superadmin{driftedCount > 0 && <> · {driftedCount} setting{driftedCount === 1 ? '' : 's'} changed from default</>}.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
          <RefreshCcw size={12} aria-hidden />
          Refresh
        </Button>
      </header>

      {/* The honest version of "takes effect immediately". */}
      <div className="flex items-start gap-2 rounded-lg border border-white/[0.06] bg-[#0d0d0d] p-3 text-xs text-zinc-400">
        <Info size={14} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden />
        <p>
          A saved value applies to <strong className="text-zinc-200">this API process immediately</strong>.
          Every other process — the other API workers and the arq settlement worker
          that pays out claims — caches settings for up to{' '}
          <strong className="text-zinc-200">
            {Math.round((data?.propagation_seconds ?? 300) / 60)} minutes
          </strong>, so a change can take that long to reach settlement.
          Kill switches are the exception: they are read fresh on every check and take effect everywhere at once.
        </p>
      </div>

      {!isSuperadmin && (
        <div className="rounded-lg border border-amber-900/50 bg-amber-950/20 p-3 text-xs text-amber-200">
          You have the <strong>admin</strong> role — settings are read-only for you.
          Changing them requires superadmin.
        </div>
      )}

      {loading && !data ? (
        <LoadingSkeleton />
      ) : (
        <>
          {killSwitches && (
            <KillSwitchPanel
              group={killSwitches}
              canEdit={isSuperadmin}
              onSave={save}
            />
          )}

          <div className="grid grid-cols-12 gap-6">
            {/* LEFT — group nav */}
            <aside className="col-span-12 lg:col-span-3">
              <div className="sticky top-6 space-y-3">
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600" aria-hidden />
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    aria-label="Search settings"
                    placeholder="Search all settings…"
                    className="w-full rounded-lg border border-white/[0.08] bg-[#0a0a0a] py-2 pl-9 pr-3 text-sm text-white placeholder:text-zinc-600 focus:border-[#f95400]/40 focus:outline-none focus:ring-1 focus:ring-[#f95400]/40"
                  />
                </div>

                <nav className="space-y-1" aria-label="Setting groups">
                  {groups.map((g) => {
                    const Icon = iconForGroup(g.name);
                    const isActive = !isFlatView && g.name === currentGroupName;
                    const count = g.settings.filter((s) => (liveOnly ? s.live : true)).length;
                    return (
                      <button
                        key={g.name}
                        type="button"
                        aria-current={isActive ? 'true' : undefined}
                        onClick={() => { setQuery(''); setActiveGroup(g.name); }}
                        className={cn(
                          'group relative flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                          isActive
                            ? 'border-transparent bg-gradient-to-r from-[#f95400]/25 via-[#f95400]/10 to-transparent text-white'
                            : 'border-transparent text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-300',
                        )}
                      >
                        {isActive && <span aria-hidden className="absolute inset-y-1 left-0 w-[3px] rounded-r bg-[#f95400]" />}
                        <Icon size={14} className={cn(isActive ? 'text-[#f95400]' : 'text-zinc-500')} aria-hidden />
                        <span className="flex-1 truncate">{g.name}</span>
                        <span className="font-mono text-[11px] text-zinc-600">{count}</span>
                      </button>
                    );
                  })}
                </nav>

                <label className="flex cursor-pointer items-center justify-between gap-2 rounded-lg border border-white/[0.08] bg-[#0d0d0d] px-3 py-2 text-xs text-zinc-300">
                  <span>Live only</span>
                  <input
                    type="checkbox"
                    checked={liveOnly}
                    onChange={(e) => setLiveOnly(e.target.checked)}
                    className="h-3.5 w-3.5 accent-[#f95400]"
                  />
                </label>
              </div>
            </aside>

            {/* RIGHT — cards */}
            <section className="col-span-12 lg:col-span-9">
              {isFlatView ? (
                <FlatResults
                  groups={flatGroups}
                  query={query}
                  liveOnly={liveOnly}
                  canEdit={isSuperadmin}
                  onClear={() => { setQuery(''); setLiveOnly(false); }}
                  onSave={save}
                />
              ) : selectedGroup ? (
                <div className="space-y-4">
                  <div>
                    <h2 className="font-syne text-lg font-semibold tracking-tight text-white">
                      {selectedGroup.name}
                    </h2>
                    <p className="mt-0.5 text-sm text-zinc-500">{selectedGroup.description}</p>
                  </div>
                  {selectedGroup.settings.length === 0 ? (
                    <EmptyState
                      icon={Settings2}
                      title="No settings to show"
                      description="All settings in this group are stored-but-not-yet-wired. Toggle Live only off to see them."
                    />
                  ) : (
                    <div className="space-y-3">
                      {selectedGroup.settings.map((s) => (
                        <SettingCard key={s.key} setting={s} onSave={save} canEdit={isSuperadmin} />
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <EmptyState
                  icon={Settings2}
                  title="No site settings defined"
                  description="Run backend/scripts/seed_settings.py to seed defaults."
                />
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Kill switches — pinned, toggles, confirmed
// ---------------------------------------------------------------------------
function KillSwitchPanel({
  group, canEdit, onSave,
}: {
  group: SiteSettingsGroup;
  canEdit: boolean;
  onSave: (s: SiteSettingRow, value: string) => Promise<SaveResult>;
}) {
  const [pending, setPending] = useState<{ setting: SiteSettingRow; next: boolean } | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const anyOff = group.settings.some(
    (s) => (s.key === 'MAINTENANCE_MODE' ? s.value === 'true' : s.value !== 'true'),
  );

  return (
    <section
      className={cn(
        'rounded-2xl border p-4',
        anyOff ? 'border-red-900/60 bg-red-950/20' : 'border-white/[0.06] bg-[#0d0d0d]',
      )}
      aria-label="Kill switches"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Power size={15} className={anyOff ? 'text-red-400' : 'text-zinc-400'} aria-hidden />
        <h2 className="font-syne text-sm font-semibold uppercase tracking-wide text-white">
          Kill switches
        </h2>
        {anyOff
          ? <Badge tone="danger">a feature is switched off</Badge>
          : <Badge tone="success">all systems go</Badge>}
      </div>
      <p className="mt-1 text-xs text-zinc-500">{group.description}</p>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {group.settings.map((s) => {
          const on = s.value.toLowerCase() === 'true';
          // MAINTENANCE_MODE reads inverted: ON means the product is DOWN.
          const bad = s.key === 'MAINTENANCE_MODE' ? on : !on;
          return (
            <div
              key={s.key}
              className={cn(
                'flex items-start justify-between gap-3 rounded-xl border p-3',
                bad ? 'border-red-900/60 bg-[#140c0c]' : 'border-white/[0.06] bg-[#111]',
              )}
            >
              <div className="min-w-0">
                <code className="block truncate font-mono text-xs text-white">{s.key}</code>
                <p className="mt-0.5 text-[11px] leading-snug text-zinc-500">{s.description}</p>
              </div>
              <BoolToggle
                id={`kill-${s.key}`}
                label={s.key}
                value={on}
                disabled={!canEdit || busyKey === s.key}
                onChange={(next) => setPending({ setting: s, next })}
              />
            </div>
          );
        })}
      </div>

      {pending && (
        <ConfirmDialog
          open
          onClose={() => setPending(null)}
          tone="danger"
          title={`Turn ${pending.setting.key} ${pending.next ? 'ON' : 'OFF'}?`}
          description={pending.setting.impact}
          confirmLabel={pending.next ? 'Turn on' : 'Turn off'}
          onConfirm={async () => {
            const { setting, next } = pending;
            setPending(null);
            setBusyKey(setting.key);
            try {
              await onSave(setting, next ? 'true' : 'false');
            } finally {
              setBusyKey(null);
            }
          }}
          details={
            <div className="space-y-2">
              <div className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 font-mono text-sm">
                <span className="text-zinc-500 line-through">{pending.setting.value}</span>
                <span className="text-zinc-600" aria-hidden>→</span>
                <span className="text-[#f95400]">{pending.next ? 'true' : 'false'}</span>
              </div>
              <p className="text-xs text-zinc-500">
                Takes effect for every process at once — kill switches bypass the settings cache.
                Work already in flight is not cancelled and no karma is refunded.
              </p>
            </div>
          }
        />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
function FlatResults({
  groups, query, liveOnly, canEdit, onClear, onSave,
}: {
  groups: SiteSettingsGroup[];
  query: string;
  liveOnly: boolean;
  canEdit: boolean;
  onClear: () => void;
  onSave: (s: SiteSettingRow, value: string) => Promise<SaveResult>;
}) {
  const total = groups.reduce((acc, g) => acc + g.settings.length, 0);

  if (total === 0) {
    return (
      <EmptyState
        icon={Settings2}
        title="No settings match"
        description={
          query
            ? `Nothing matches “${query}”${liveOnly ? ' (with Live only on)' : ''}. Try a broader search.`
            : 'No live settings — toggle Live only off to see stored-but-not-yet-wired ones.'
        }
        action={<Button variant="secondary" size="sm" onClick={onClear}>Clear filters</Button>}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="text-[11px] text-zinc-600">
        {total} setting{total === 1 ? '' : 's'}
        {query && <> matching <span className="font-mono text-zinc-400">{query}</span></>}
        {liveOnly && <> · live only</>}
      </div>

      {groups.map((g) => {
        const Icon = iconForGroup(g.name);
        return (
          <section key={g.name} className="space-y-3">
            <div className="flex items-center gap-2">
              <Icon size={14} className="text-zinc-500" aria-hidden />
              <h3 className="font-syne text-sm font-semibold uppercase tracking-wide text-zinc-400">{g.name}</h3>
              <span className="font-mono text-[11px] text-zinc-600">{g.settings.length}</span>
            </div>
            <div className="space-y-3">
              {g.settings.map((s) => (
                <SettingCard key={s.key} setting={s} onSave={onSave} canEdit={canEdit} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-28 w-full" />
      <div className="grid grid-cols-12 gap-6">
        <aside className="col-span-12 space-y-2 lg:col-span-3">
          {Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
        </aside>
        <section className="col-span-12 space-y-3 lg:col-span-9">
          <Skeleton className="h-5 w-48" />
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28 w-full" />)}
        </section>
      </div>
    </div>
  );
}
