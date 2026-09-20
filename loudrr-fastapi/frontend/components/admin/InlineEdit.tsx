'use client';

import { useCallback, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Check, Loader2, Pencil, X } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

type Kind = 'text' | 'number' | 'bool';

export type InlineEditValue = string | number | boolean;

interface InlineEditProps {
  value: InlineEditValue;
  onSave: (newValue: InlineEditValue) => Promise<void>;
  kind?: Kind;
  placeholder?: string;
  /** Force textarea rendering. Auto-detected for newline / >80 char text values. */
  multiline?: boolean;
  /**
   * What is being edited, for screen readers — e.g. the setting key. Becomes
   * "Edit SCORE_REFRESH_COOLDOWN_MINUTES" on the trigger and labels the field.
   */
  label?: string;
}

function toInputString(v: InlineEditValue, kind: Kind): string {
  if (kind === 'bool') return v ? 'true' : 'false';
  if (v === null || v === undefined) return '';
  return String(v);
}

/**
 * Parse the draft for committing.
 *
 * Returns `null` when a number field is empty or unparseable. That case USED
 * to fall through to `0` — and because blur committed unconditionally, tabbing
 * out of a cleared money field silently wrote 0 to it (e.g. a karma-per-post
 * or a cooldown). Now it aborts the commit and shows an inline error instead.
 */
function parseInputString(s: string, kind: Kind): InlineEditValue | null {
  if (kind === 'number') {
    const trimmed = s.trim();
    if (trimmed === '') return null;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : null;
  }
  if (kind === 'bool') return s === 'true';
  return s;
}

function displayString(v: InlineEditValue, kind: Kind, placeholder?: string): string {
  if (kind === 'bool') return v ? 'true' : 'false';
  const s = v === null || v === undefined ? '' : String(v);
  if (!s) return placeholder ?? '—';
  return s;
}

/** A string value is "long" if it contains a newline or exceeds 80 chars. */
function isLongText(v: InlineEditValue, kind: Kind): boolean {
  if (kind !== 'text') return false;
  const s = typeof v === 'string' ? v : String(v ?? '');
  return s.includes('\n') || s.length > 80;
}

export function InlineEdit({
  value,
  onSave,
  kind = 'text',
  placeholder,
  multiline,
  label,
}: InlineEditProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(() => toInputString(value, kind));
  const [saving, setSaving] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  // Set once we've focused this editor session, so re-renders (typing) don't
  // yank the caret back to the start.
  const focusedRef = useRef(false);
  // Synchronous double-submit guard: two fast clicks on Save both read the
  // same (not-yet-committed) `saving` state.
  const savingRef = useRef(false);

  // Resolve textarea vs input: explicit prop wins; otherwise auto-detect from
  // current value (text kind only).
  const useTextarea = kind === 'text' && (multiline === true || isLongText(value, kind));

  // NOTE: no "sync draft from value" effect. `draft` is only ever read while
  // editing, and `beginEdit` seeds it from the current value every time — so
  // the old effect was redundant, and it fought the draft on re-render.

  /**
   * Focus via ref CALLBACK, not an effect.
   *
   * The editor used to live inside <AnimatePresence mode="wait">, which holds
   * the incoming branch back until the outgoing one finishes exiting — so the
   * focus effect ran while `inputRef.current` was still null and the focus was
   * simply dropped. One click produced a stuck, unfocused editor and the whole
   * settings page read as uneditable. A callback ref fires the instant the
   * node mounts, whenever that turns out to be.
   */
  const attachInput = useCallback(
    (el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null) => {
      inputRef.current = el;
      if (!el || focusedRef.current) return;
      focusedRef.current = true;
      el.focus();
      if ('select' in el && typeof el.select === 'function') {
        try {
          el.select();
        } catch {
          /* some input types reject .select() */
        }
      }
    },
    [],
  );

  const isMono = kind !== 'bool';
  // Callers that know what they're editing should pass `label` (e.g. the
  // setting key). Without it, fold the current value into the name so a screen
  // reader doesn't read a page of identical "Edit value" buttons.
  const fieldLabel = label
    ? `Edit ${label}`
    : `Edit value (currently ${displayString(value, kind, placeholder)})`;

  const beginEdit = () => {
    if (saving) return;
    setDraft(toInputString(value, kind));
    setFieldError(null);
    focusedRef.current = false;
    setEditing(true);
  };

  const closeEditor = useCallback(() => {
    setEditing(false);
    setFieldError(null);
    focusedRef.current = false;
    // Put the keyboard back on the trigger. Two frames: the async save path
    // closes from a promise continuation, so the display button may not be
    // committed to the DOM yet on the first one.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => triggerRef.current?.focus());
    });
  }, []);

  const cancel = () => {
    setDraft(toInputString(value, kind));
    closeEditor();
  };

  const commit = async () => {
    if (savingRef.current) return;

    const parsed = parseInputString(draft, kind);
    if (parsed === null) {
      // Abort — never write a fabricated 0. The editor stays open with the
      // bad draft intact so the admin can see and fix what they typed.
      setFieldError(
        draft.trim() === '' ? 'Enter a number — blank is not saved' : `"${draft.trim()}" is not a number`,
      );
      inputRef.current?.focus();
      return;
    }
    setFieldError(null);

    // No-op if unchanged.
    if (parsed === value) {
      closeEditor();
      return;
    }

    savingRef.current = true;
    setSaving(true);
    try {
      await onSave(parsed);
      savingRef.current = false;
      setSaving(false);
      closeEditor();
    } catch (err) {
      savingRef.current = false;
      setSaving(false);
      const msg = err instanceof Error ? err.message : 'Failed to save';
      // Pages may throw a blank-message sentinel when they've already toasted.
      if (msg) toast.error(msg);
      // Revert draft back to original.
      setDraft(toInputString(value, kind));
      closeEditor();
    }
  };

  const onKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => {
    if (e.key === 'Enter' && (!useTextarea || e.metaKey || e.ctrlKey)) {
      // Single-line: Enter saves. Textarea: Enter is a newline, Cmd/Ctrl+Enter
      // saves (the Save button is always there as the non-keyboard path).
      e.preventDefault();
      void commit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancel();
    }
  };

  // For textarea auto-sizing: at least 2 rows, grow with newline count.
  const textareaRows = (() => {
    if (!useTextarea) return 2;
    const lines = draft.split('\n').length;
    return Math.max(2, lines);
  })();

  const fieldClasses = cn(
    'rounded-md border bg-[#0a0a0a] text-xs text-white placeholder:text-zinc-500 focus:outline-none focus:ring-1 disabled:opacity-50',
    fieldError
      ? 'border-red-600/70 focus:ring-red-600/40'
      : 'border-[#f95400]/40 focus:ring-[#f95400]/40',
  );

  // ---- Editing ----
  // NOTE: no <AnimatePresence mode="wait"> here — see attachInput above. A
  // plain conditional mounts the field synchronously, which is what makes the
  // focus (and therefore the whole editor) work.
  if (editing) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.98 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.12 }}
        className={cn(useTextarea ? 'flex w-full flex-col gap-1.5' : 'inline-flex flex-col gap-1.5')}
      >
        <div
          className={cn(
            'gap-1.5',
            useTextarea ? 'flex flex-col' : 'flex flex-wrap items-center',
          )}
        >
          {kind === 'bool' ? (
            <select
              ref={attachInput}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={saving}
              aria-label={fieldLabel}
              className={cn(fieldClasses, 'px-2 py-1')}
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : useTextarea ? (
            <textarea
              ref={attachInput}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              disabled={saving}
              rows={textareaRows}
              aria-label={fieldLabel}
              aria-invalid={fieldError ? true : undefined}
              // `field-sizing: content` lets supporting browsers grow the
              // textarea to fit content; `rows` is the fallback floor.
              style={{ fieldSizing: 'content' } as React.CSSProperties}
              className={cn(fieldClasses, 'w-full resize-y px-2 py-1.5', isMono && 'font-mono')}
            />
          ) : (
            <input
              ref={attachInput}
              type={kind === 'number' ? 'number' : 'text'}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              disabled={saving}
              aria-label={fieldLabel}
              aria-invalid={fieldError ? true : undefined}
              className={cn(fieldClasses, 'px-2 py-1', isMono && 'font-mono')}
            />
          )}

          {/* Explicit controls: committing must never depend on focus. Blur
              deliberately does NOTHING now — it used to commit, which is how
              a cleared number field wrote 0 and how clicking "Cancel" could
              still save. */}
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              // onMouseDown-preventDefault keeps the field focused so the
              // caret doesn't jump before we read the draft.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => void commit()}
              disabled={saving}
              aria-label={label ? `Save ${label}` : 'Save'}
              title="Save (Enter)"
              className="inline-flex h-7 min-w-[28px] items-center justify-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/15 px-2 text-[11px] font-semibold text-emerald-200 transition-colors hover:bg-emerald-500/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/40 disabled:opacity-50"
            >
              {saving ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Check size={12} aria-hidden />
              )}
              <span>Save</span>
            </button>
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={cancel}
              disabled={saving}
              aria-label={label ? `Cancel editing ${label}` : 'Cancel'}
              title="Cancel (Esc)"
              className="inline-flex h-7 min-w-[28px] items-center justify-center rounded-md border border-white/[0.08] bg-white/[0.04] px-2 text-[11px] font-medium text-zinc-300 transition-colors hover:bg-white/[0.08] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 disabled:opacity-50"
            >
              <X size={12} aria-hidden />
              <span className="sr-only">Cancel</span>
            </button>
          </div>
        </div>

        {fieldError && (
          <p role="alert" className="text-[11px] font-medium text-red-400">
            {fieldError}
          </p>
        )}
      </motion.div>
    );
  }

  // ---- Display ----
  return (
    <motion.button
      ref={triggerRef}
      type="button"
      onClick={beginEdit}
      initial={false}
      whileHover={{ scale: 1.01 }}
      whileTap={{ scale: 0.98 }}
      aria-label={fieldLabel}
      className={cn(
        'group relative min-h-[28px] rounded-md border border-white/[0.06] bg-white/[0.03] px-2 py-1 text-xs text-zinc-200 transition-colors hover:border-[#f95400]/30 hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f95400]/40',
        useTextarea ? 'flex w-full items-start gap-1.5 text-left' : 'inline-flex items-center gap-1.5',
        isMono && 'font-mono',
      )}
      title="Click to edit"
    >
      <span className={cn(useTextarea && 'flex-1 whitespace-pre-wrap break-words')}>
        {displayString(value, kind, placeholder)}
      </span>
      {/* Always visible (was opacity-0 until hover) — on a touch screen there
          is no hover, so the panel gave admins no hint anything was editable. */}
      <Pencil
        size={10}
        className={cn(
          'shrink-0 text-[#f95400] opacity-70 transition-opacity group-hover:opacity-100',
          useTextarea && 'mt-0.5',
        )}
        aria-hidden
      />
    </motion.button>
  );
}
