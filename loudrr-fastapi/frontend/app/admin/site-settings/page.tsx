'use client';

import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import {
  AlertTriangle,
  Coins,
  Crown,
  Flame,
  Megaphone,
  RefreshCcw,
  Search,
  Settings2,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import {
  adminApi,
  SiteSettingRow,
  SiteSettingsGroup,
} from '@/lib/api';
import { Button } from '@/components/admin/Button';
import { Badge } from '@/components/admin/Badge';
import { EmptyState } from '@/components/admin/EmptyState';
import { Skeleton } from '@/components/admin/Skeleton';
import { InlineEdit, InlineEditValue } from '@/components/admin/InlineEdit';
import { cn } from '@/lib/utils';

// Map group names to lucide icons. Matches names emitted by the backend.
const GROUP_ICONS: Record<string, LucideIcon> = {
  Economy: Coins,
  Verification: ShieldCheck,
  'Tier thresholds': Crown,
  'Tier multipliers': TrendingUp,
  Streaks: Flame,
  Sponsored: Megaphone,
  'Karma decay': TrendingDown,
};

function iconForGroup(name: string): LucideIcon {
  return GROUP_ICONS[name] ?? Settings2;
}

function inlineKind(dt: SiteSettingRow['data_type']): 'text' | 'number' | 'bool' {
  if (dt === 'bool') return 'bool';
  if (dt === 'int' || dt === 'float' || dt === 'decimal') return 'number';
  return 'text';
}

export default function SiteSettingsPage() {
  const [groups, setGroups] = useState<SiteSettingsGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [liveOnly, setLiveOnly] = useState(false);
  const [activeGroup, setActiveGroup] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const res = await adminApi.getSiteSettings();
      setGroups(res.groups);
      // Initialize active group on first successful load.
      setActiveGroup((prev) => {
        if (prev && res.groups.some((g) => g.name === prev)) return prev;
        return res.groups[0]?.name ?? null;
      });
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      toast.error('Failed to load site settings', { description: msg });
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apply Live-only filter to a group's settings (without considering search).
  const liveFilter = (s: SiteSettingRow) => (liveOnly ? s.live : true);

  // Right-pane content: either selected group's settings, or a flat search/live-only view.
  const isFlatView = query.trim().length > 0 || liveOnly;

  const flatGroups = useMemo<SiteSettingsGroup[]>(() => {
    if (!groups) return [];
    const q = query.trim().toLowerCase();
    return groups
      .map((g) => ({
        ...g,
        settings: g.settings.filter((s) => {
          if (liveOnly && !s.live) return false;
          if (!q) return true;
          return (
            s.key.toLowerCase().includes(q) ||
            s.description.toLowerCase().includes(q)
          );
        }),
      }))
      .filter((g) => g.settings.length > 0);
  }, [groups, query, liveOnly]);

  const selectedGroup = useMemo<SiteSettingsGroup | null>(() => {
    if (!groups || !activeGroup) return null;
    const g = groups.find((x) => x.name === activeGroup);
    if (!g) return null;
    return { ...g, settings: g.settings.filter(liveFilter) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groups, activeGroup, liveOnly]);

  // Per-group counts for the nav (respecting Live-only toggle so the number
  // matches what the right pane will show when that group is selected).
  const groupCounts = useMemo(() => {
    const m: Record<string, number> = {};
    if (!groups) return m;
    for (const g of groups) {
      m[g.name] = g.settings.filter(liveFilter).length;
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groups, liveOnly]);

  async function saveSetting(s: SiteSettingRow, next: InlineEditValue): Promise<void> {
    const key = s.key;
    const valueStr = typeof next === 'boolean' ? (next ? 'true' : 'false') : String(next);
    try {
      await adminApi.updateSiteSetting(key, valueStr);
      toast.success(`Updated ${key}`, {
        description: s.live
          ? 'Live setting — change takes effect immediately.'
          : 'Stored. Will apply once backend code wires this setting.',
      });
      // Refetch all settings so other groups/cards reflect any cross-effects.
      await load();
    } catch (e) {
      const msg = (e as Error).message;
      if (msg.startsWith('403')) {
        toast.error('Superadmin required to edit settings');
      } else if (msg.startsWith('422') || msg.startsWith('400')) {
        toast.error(`Invalid value for ${key}`, {
          description: msg.replace(/^\d+:\s*/, ''),
        });
      } else {
        toast.error(`Failed to update ${key}`, { description: msg });
      }
      // Re-throw a quiet sentinel so InlineEdit reverts its draft without
      // showing a second toast with the raw "<status>: <detail>" message.
      throw new SilentSaveError();
    }
  }

  // ---- Render ----

  if (error && !groups) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="Couldn't load site settings"
        description={
          error.startsWith('403')
            ? "You're authenticated but lack admin role. Bootstrap via backend/scripts/seed_admins.py."
            : error
        }
        action={
          <Button variant="secondary" size="sm" onClick={load}>
            <RefreshCcw size={12} />
            Retry
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-syne text-2xl font-bold tracking-tight">Site Settings</h1>
          <p className="mt-1 max-w-2xl text-sm text-zinc-500">
            Tune the karma economy, tiers, and verification policy. Edits require
            superadmin and take effect immediately for live settings.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={load} disabled={groups === null}>
          <RefreshCcw size={12} />
          Refresh
        </Button>
      </div>

      {groups === null ? (
        <LoadingSkeleton />
      ) : (
        <div className="grid grid-cols-12 gap-6">
          {/* LEFT — Category nav */}
          <aside className="col-span-12 lg:col-span-3">
            <div className="sticky top-6 space-y-3">
              {/* Search */}
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600"
                />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search all settings…"
                  className="w-full rounded-lg border border-white/[0.08] bg-[#0a0a0a] py-2 pl-9 pr-3 text-sm text-white placeholder:text-zinc-600 focus:border-[#f95400]/40 focus:outline-none focus:ring-1 focus:ring-[#f95400]/40"
                />
              </div>

              {/* Group list */}
              <nav className="space-y-1">
                {groups.map((g) => {
                  const Icon = iconForGroup(g.name);
                  const isActive = !isFlatView && g.name === activeGroup;
                  const count = groupCounts[g.name] ?? 0;
                  return (
                    <button
                      key={g.name}
                      type="button"
                      onClick={() => {
                        // Clicking a group clears search so the right pane
                        // switches back to the per-group view as advertised.
                        setQuery('');
                        setActiveGroup(g.name);
                      }}
                      className={cn(
                        'group relative flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                        isActive
                          ? 'border-transparent bg-gradient-to-r from-[#f95400]/25 via-[#f95400]/10 to-transparent text-white'
                          : 'border-transparent text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-300',
                      )}
                    >
                      {isActive && (
                        <span
                          aria-hidden
                          className="absolute inset-y-1 left-0 w-[3px] rounded-r bg-[#f95400]"
                        />
                      )}
                      <Icon
                        size={14}
                        className={cn(isActive ? 'text-[#f95400]' : 'text-zinc-500')}
                      />
                      <span className="flex-1 truncate">{g.name}</span>
                      <span className="font-mono text-[11px] text-zinc-600">{count}</span>
                    </button>
                  );
                })}
              </nav>

              {/* Live-only toggle */}
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

          {/* RIGHT — Settings pane */}
          <section className="col-span-12 lg:col-span-9">
            {isFlatView ? (
              <FlatResults
                groups={flatGroups}
                query={query}
                liveOnly={liveOnly}
                onClear={() => {
                  setQuery('');
                  setLiveOnly(false);
                }}
                onSave={saveSetting}
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
                    description={
                      liveOnly
                        ? 'All settings in this group are stored-but-not-yet-wired. Toggle Live only off to see them.'
                        : 'This group has no settings defined.'
                    }
                  />
                ) : (
                  <div className="space-y-3">
                    {selectedGroup.settings.map((s) => (
                      <SettingCard key={s.key} setting={s} onSave={saveSetting} />
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
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

class SilentSaveError extends Error {
  constructor() {
    super('');
    this.name = 'SilentSaveError';
  }
}

interface SaveFn {
  (s: SiteSettingRow, next: InlineEditValue): Promise<void>;
}

function SettingCard({ setting, onSave }: { setting: SiteSettingRow; onSave: SaveFn }) {
  const kind = inlineKind(setting.data_type);
  // Coerce the displayed value to its typed form for InlineEdit.
  const typedValue: InlineEditValue =
    kind === 'number'
      ? Number(setting.value)
      : kind === 'bool'
        ? setting.value === 'true'
        : setting.value;

  return (
    <motion.div
      whileHover={{ scale: 1.005 }}
      transition={{ duration: 0.12 }}
      className="rounded-xl border border-white/[0.06] bg-[#111] p-4 transition-colors hover:border-white/[0.12]"
    >
      <div className="flex flex-wrap items-center gap-2">
        <code className="font-mono text-sm text-white">{setting.key}</code>
        {setting.live ? (
          <Badge tone="success">live</Badge>
        ) : (
          <Badge tone="warning">stored</Badge>
        )}
        {setting.value !== setting.default && (
          <span className="text-[11px] text-zinc-600">
            default: <span className="font-mono">{setting.default}</span>
          </span>
        )}
        {!setting.persisted && (
          <span className="text-[11px] text-amber-400/80">(using default — not yet stored)</span>
        )}
      </div>

      {setting.description && (
        <p className="mt-1 text-sm text-zinc-400">{setting.description}</p>
      )}

      <div className="mt-3">
        <InlineEdit
          value={typedValue}
          kind={kind}
          placeholder={setting.default}
          multiline={
            setting.data_type === 'str' &&
            (setting.value.includes(String.fromCharCode(10)) || setting.value.length > 80)
          }
          onSave={(next) => onSave(setting, next)}
        />
      </div>
    </motion.div>
  );
}

function FlatResults({
  groups,
  query,
  liveOnly,
  onClear,
  onSave,
}: {
  groups: SiteSettingsGroup[];
  query: string;
  liveOnly: boolean;
  onClear: () => void;
  onSave: SaveFn;
}) {
  const total = groups.reduce((acc, g) => acc + g.settings.length, 0);

  if (total === 0) {
    return (
      <EmptyState
        icon={Settings2}
        title="No settings match"
        description={
          query
            ? `Nothing matches "${query}"${liveOnly ? ' (with Live only on)' : ''}. Try a broader search.`
            : 'No live settings — toggle Live only off to see stored-but-not-yet-wired ones.'
        }
        action={
          <Button variant="secondary" size="sm" onClick={onClear}>
            Clear filters
          </Button>
        }
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
              <Icon size={14} className="text-zinc-500" />
              <h3 className="font-syne text-sm font-semibold uppercase tracking-wide text-zinc-400">
                {g.name}
              </h3>
              <span className="font-mono text-[11px] text-zinc-600">{g.settings.length}</span>
            </div>
            <div className="space-y-3">
              {g.settings.map((s) => (
                <SettingCard key={s.key} setting={s} onSave={onSave} />
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
    <div className="grid grid-cols-12 gap-6">
      <aside className="col-span-12 lg:col-span-3">
        <div className="space-y-2">
          <Skeleton className="h-9 w-full" />
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      </aside>
      <section className="col-span-12 space-y-3 lg:col-span-9">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-3 w-72" />
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </section>
    </div>
  );
}
