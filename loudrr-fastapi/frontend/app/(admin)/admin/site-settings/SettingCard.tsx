'use client';

/**
 * One setting card: the value editor, the bounds the server actually enforces,
 * the shipped default, a reset, and the change history.
 *
 * Notes that matter:
 *  - `min`/`max`/`step` come from the SAME spec the PUT validates against, so
 *    the input attributes and the server rule can't drift apart. The inline
 *    check runs before the request, and a server 422 lands in the same place
 *    (the field), not only in a toast.
 *  - `danger` settings are confirmed with an explicit old → new line plus the
 *    spec's one-line impact statement before anything is saved.
 *  - Saving is reported ONCE. The old page toasted an error and then threw a
 *    `SilentSaveError` with an empty message, which the editor toasted again —
 *    that is why a failed save produced two toasts, one of them blank.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { History, RotateCcw, X } from 'lucide-react';

import { adminApi, type SiteSettingHistoryRow, type SiteSettingRow } from '@/lib/api';
import { Badge } from '@/components/admin/Badge';
import { Button } from '@/components/admin/Button';
import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { absolute, timeAgo } from '@/lib/admin/format';
import { cn } from '@/lib/utils';

export type SaveResult = { ok: true } | { ok: false; message: string };
export type SaveSetting = (s: SiteSettingRow, value: string) => Promise<SaveResult>;

/** Mirror of core/site_settings_meta.validate_value — same rules, client side. */
export function validateSetting(s: SiteSettingRow, raw: string): string | null {
  if (raw.length > 255) return 'Too long (max 255 characters).';

  if (s.data_type === 'bool') {
    return ['true', 'false', '1', '0', 'yes', 'no', 'on', 'off'].includes(raw.trim().toLowerCase())
      ? null
      : 'Must be true or false.';
  }
  if (s.data_type === 'str') return null;

  if (raw.trim() === '') return 'Required.';
  const n = Number(raw);
  if (!Number.isFinite(n)) return 'Must be a number.';
  if (s.data_type === 'int' && !Number.isInteger(n)) return 'Must be a whole number.';

  const unit = s.unit ? ` ${s.unit}` : '';
  if (s.min !== null && n < s.min) return `Must be at least ${fmtBound(s.min)}${unit}.`;
  if (s.max !== null && n > s.max) return `Must be at most ${fmtBound(s.max)}${unit}.`;
  return null;
}

function fmtBound(n: number): string {
  return Number.isInteger(n) ? String(n) : String(n);
}

const MULTILINE_AT = 80;

export function SettingCard({
  setting,
  onSave,
  canEdit,
}: {
  setting: SiteSettingRow;
  onSave: SaveSetting;
  canEdit: boolean;
}) {
  const [draft, setDraft] = useState(setting.value);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<null | { next: string; reset?: boolean }>(null);
  const [showHistory, setShowHistory] = useState(false);

  // A refetch (or another admin's change) must not stomp an in-progress edit,
  // so only re-seed when the incoming value actually changed.
  useEffect(() => { setDraft(setting.value); setError(null); }, [setting.value]);

  const dirty = draft !== setting.value;
  const multiline =
    setting.data_type === 'str' &&
    (setting.value.includes('\n') || setting.value.length > MULTILINE_AT);

  const commit = useCallback(async (next: string) => {
    setBusy(true);
    try {
      const res = await onSave(setting, next);
      // Exactly one report per save. A failure puts the reason in the field
      // too, so the admin can see it next to the input they have to fix.
      if (!res.ok) setError(res.message);
      else setError(null);
    } finally {
      setBusy(false);
    }
  }, [onSave, setting]);

  function attemptSave(next: string, reset = false) {
    const problem = validateSetting(setting, next);
    if (problem) { setError(problem); return; }
    setError(null);
    if (setting.danger) setConfirming({ next, reset });
    else void commit(next);
  }

  return (
    <div
      className={cn(
        'rounded-xl border bg-[#111] p-4 transition-colors',
        setting.danger ? 'border-amber-900/40 hover:border-amber-800/60' : 'border-white/[0.06] hover:border-white/[0.12]',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <code className="font-mono text-sm text-white">{setting.key}</code>
        {setting.live ? <Badge tone="success">live</Badge> : <Badge tone="warning">stored</Badge>}
        {setting.danger && <Badge tone="danger">confirm to save</Badge>}
        {!setting.persisted && (
          <span className="text-[11px] text-amber-400/80">(using default — not yet stored)</span>
        )}

        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            aria-expanded={showHistory}
            aria-label={`Change history for ${setting.key}`}
            title="Change history"
            className={cn(
              'rounded-md p-1.5 transition-colors',
              showHistory ? 'bg-white/[0.08] text-zinc-200' : 'text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200',
            )}
          >
            <History size={13} aria-hidden />
          </button>
          {setting.drifted && canEdit && (
            <button
              type="button"
              onClick={() => attemptSave(setting.default, true)}
              aria-label={`Reset ${setting.key} to its default of ${setting.default}`}
              title={`Reset to default (${setting.default})`}
              className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              <RotateCcw size={13} aria-hidden />
            </button>
          )}
        </div>
      </div>

      {setting.description && <p className="mt-1 text-sm text-zinc-400">{setting.description}</p>}

      {/* the default is ALWAYS visible, drifted or not */}
      <p className="mt-1 text-[11px] text-zinc-600">
        default <span className="font-mono text-zinc-500">{setting.default}</span>
        {setting.drifted && <span className="ml-2 text-amber-400/80">· changed from default</span>}
        {(setting.min !== null || setting.max !== null) && (
          <span className="ml-2">
            · allowed {setting.min !== null ? fmtBound(setting.min) : '−∞'}–
            {setting.max !== null ? fmtBound(setting.max) : '∞'}
            {setting.unit ? ` ${setting.unit}` : ''}
          </span>
        )}
      </p>

      {/* ---- editor ---- */}
      <div className="mt-3">
        {setting.data_type === 'bool' ? (
          <BoolToggle
            id={`set-${setting.key}`}
            value={setting.value.toLowerCase() === 'true'}
            disabled={!canEdit || busy}
            label={setting.key}
            onChange={(next) => attemptSave(next ? 'true' : 'false')}
          />
        ) : (
          <form
            className="flex flex-wrap items-start gap-2"
            // noValidate: min/max stay on the input (spinner steps, mobile
            // keypads, assistive tech) but the browser's native bubble is
            // suppressed so ONE message is shown — ours, which is worded the
            // same as the server's and is where a server 422 lands too.
            noValidate
            onSubmit={(e) => { e.preventDefault(); attemptSave(draft); }}
          >
            <div className="min-w-[180px] flex-1">
              <label htmlFor={`set-${setting.key}`} className="sr-only">{setting.key}</label>
              <div className="relative">
                {multiline ? (
                  <textarea
                    id={`set-${setting.key}`}
                    value={draft}
                    rows={3}
                    disabled={!canEdit || busy}
                    maxLength={255}
                    aria-invalid={Boolean(error)}
                    onChange={(e) => { setDraft(e.target.value); setError(null); }}
                    className={fieldClass(error)}
                  />
                ) : (
                  <input
                    id={`set-${setting.key}`}
                    value={draft}
                    disabled={!canEdit || busy}
                    type={setting.data_type === 'str' ? 'text' : 'number'}
                    inputMode={setting.data_type === 'int' ? 'numeric' : 'decimal'}
                    min={setting.min ?? undefined}
                    max={setting.max ?? undefined}
                    step={setting.step ?? undefined}
                    maxLength={setting.data_type === 'str' ? 255 : undefined}
                    aria-invalid={Boolean(error)}
                    aria-describedby={error ? `set-${setting.key}-err` : undefined}
                    onChange={(e) => { setDraft(e.target.value); setError(null); }}
                    className={cn(fieldClass(error), setting.unit && 'pr-16')}
                  />
                )}
                {setting.unit && !multiline && (
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-zinc-600">
                    {setting.unit}
                  </span>
                )}
              </div>
              {error && (
                <p id={`set-${setting.key}-err`} className="mt-1 text-xs text-red-400">{error}</p>
              )}
            </div>

            <Button type="submit" size="sm" variant="primary" disabled={!canEdit || !dirty} loading={busy}>
              Save
            </Button>
            {dirty && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => { setDraft(setting.value); setError(null); }}
              >
                Cancel
              </Button>
            )}
          </form>
        )}
      </div>

      {showHistory && (
        <HistoryPanel settingKey={setting.key} onClose={() => setShowHistory(false)} />
      )}

      {confirming && (
        <ConfirmDialog
          open
          onClose={() => setConfirming(null)}
          tone="danger"
          title={confirming.reset ? `Reset ${setting.key} to default?` : `Change ${setting.key}?`}
          description={setting.impact || 'This setting affects live karma math.'}
          confirmLabel={confirming.reset ? 'Reset' : 'Save change'}
          onConfirm={async () => {
            const next = confirming.next;
            setConfirming(null);
            await commit(next);
          }}
          details={
            <div className="space-y-2">
              <div className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 font-mono text-sm">
                <span className="text-zinc-500 line-through">{setting.value || '(empty)'}</span>
                <span className="text-zinc-600" aria-hidden>→</span>
                <span className="text-[#f95400]">{confirming.next || '(empty)'}</span>
                {setting.unit && <span className="text-xs text-zinc-600">{setting.unit}</span>}
              </div>
              <p className="text-xs text-zinc-500">
                {setting.live
                  ? 'Applies to this API process immediately. Other processes — including the arq settlement worker — re-read it within 5 minutes.'
                  : 'Stored only. No backend code reads this setting yet.'}
              </p>
            </div>
          }
        />
      )}
    </div>
  );
}

function fieldClass(error: string | null) {
  return cn(
    'w-full rounded-lg border bg-[#0a0a0a] px-3 py-2 font-mono text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 disabled:opacity-50',
    error
      ? 'border-red-800/70 focus:border-red-700 focus:ring-red-700/40'
      : 'border-white/[0.08] focus:border-[#f95400]/40 focus:ring-[#f95400]/40',
  );
}

export function BoolToggle({
  id, value, onChange, disabled, label,
}: {
  id: string;
  value: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={value}
      aria-label={`${label}: ${value ? 'on' : 'off'}`}
      disabled={disabled}
      onClick={() => onChange(!value)}
      className={cn(
        'inline-flex items-center gap-2 rounded-full border px-1 py-1 transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        value ? 'border-emerald-800/70 bg-emerald-950/50' : 'border-white/[0.08] bg-[#0a0a0a]',
      )}
    >
      <span
        aria-hidden
        className={cn(
          'block h-4 w-4 rounded-full transition-transform',
          value ? 'translate-x-4 bg-emerald-400' : 'translate-x-0 bg-zinc-600',
        )}
      />
      <span className={cn('px-2 text-xs font-medium', value ? 'text-emerald-300' : 'text-zinc-500')}>
        {value ? 'ON' : 'OFF'}
      </span>
    </button>
  );
}

function HistoryPanel({ settingKey, onClose }: { settingKey: string; onClose: () => void }) {
  const [rows, setRows] = useState<SiteSettingHistoryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // No reset-setState here: the panel mounts fresh each time it is opened,
  // so `rows`/`error` already start null. Only the async callbacks set state.
  useEffect(() => {
    let cancelled = false;
    adminApi.getSiteSettingHistory(settingKey)
      .then((res) => { if (!cancelled) setRows(res.rows); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [settingKey]);

  const body = useMemo(() => {
    if (error) return <p className="text-xs text-red-400">{error}</p>;
    if (rows === null) return <p className="text-xs text-zinc-500">Loading history…</p>;
    if (rows.length === 0) {
      return <p className="text-xs text-zinc-500">Never changed — still on the shipped default.</p>;
    }
    return (
      <ol className="space-y-1.5">
        {rows.map((r) => (
          <li key={r.id} className="flex flex-wrap items-baseline gap-x-2 text-xs">
            <span className="text-zinc-500" title={absolute(r.created_at, { seconds: true })}>
              {timeAgo(r.created_at)}
            </span>
            <span className="font-mono text-zinc-600 line-through">{r.old_value ?? '(unset)'}</span>
            <span className="text-zinc-700" aria-hidden>→</span>
            <span className="font-mono text-zinc-300">{r.new_value ?? '(unset)'}</span>
            <span className="text-zinc-600">
              by {r.actor_handle ? `@${r.actor_handle}` : (r.actor_id ? `${r.actor_id.slice(0, 8)}…` : 'system')}
            </span>
          </li>
        ))}
      </ol>
    );
  }, [rows, error]);

  return (
    <div className="mt-3 rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Change history</h4>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close change history"
          className="rounded p-0.5 text-zinc-600 hover:bg-white/[0.06] hover:text-zinc-300"
        >
          <X size={12} aria-hidden />
        </button>
      </div>
      {body}
    </div>
  );
}
