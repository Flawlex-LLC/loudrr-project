'use client';

import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Loader2, Pencil } from 'lucide-react';
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
}

function toInputString(v: InlineEditValue, kind: Kind): string {
  if (kind === 'bool') return v ? 'true' : 'false';
  if (v === null || v === undefined) return '';
  return String(v);
}

function parseInputString(s: string, kind: Kind): InlineEditValue {
  if (kind === 'number') {
    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
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
}: InlineEditProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(() => toInputString(value, kind));
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<
    HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null
  >(null);
  // Guard so that an Enter-driven save doesn't double-fire via the subsequent blur.
  const committedRef = useRef(false);

  // Resolve textarea vs input: explicit prop wins; otherwise auto-detect from
  // current value (text kind only).
  const useTextarea = kind === 'text' && (multiline === true || isLongText(value, kind));

  // Sync external value changes when not editing.
  useEffect(() => {
    if (!editing) setDraft(toInputString(value, kind));
  }, [value, kind, editing]);

  // Autofocus when entering edit mode.
  useEffect(() => {
    if (editing) {
      committedRef.current = false;
      const el = inputRef.current;
      if (el) {
        el.focus();
        if ('select' in el && typeof el.select === 'function') {
          try {
            el.select();
          } catch {
            /* no-op for select elements that don't support .select() */
          }
        }
      }
    }
  }, [editing]);

  const isMono = kind !== 'bool';

  const beginEdit = () => {
    if (saving) return;
    setDraft(toInputString(value, kind));
    setEditing(true);
  };

  const cancel = () => {
    setDraft(toInputString(value, kind));
    setEditing(false);
  };

  const commit = async () => {
    if (committedRef.current) return;
    committedRef.current = true;

    const parsed = parseInputString(draft, kind);
    // No-op if unchanged.
    if (parsed === value) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onSave(parsed);
      setEditing(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to save';
      toast.error(msg);
      // Revert draft back to original.
      setDraft(toInputString(value, kind));
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const onKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => {
    if (e.key === 'Enter' && !useTextarea) {
      // In single-line inputs, Enter commits. In textarea, Enter inserts a
      // newline (commit happens on blur).
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

  return (
    <AnimatePresence mode="wait" initial={false}>
      {editing ? (
        <motion.div
          key="edit"
          initial={{ opacity: 0, scale: 0.98 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.98 }}
          transition={{ duration: 0.12 }}
          className={cn(
            useTextarea ? 'flex flex-col gap-1.5 w-full' : 'inline-flex items-center gap-1.5',
          )}
        >
          {kind === 'bool' ? (
            <select
              ref={(el) => {
                inputRef.current = el;
              }}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => void commit()}
              onKeyDown={onKeyDown}
              disabled={saving}
              className="rounded-md border border-[#f95400]/40 bg-[#0a0a0a] px-2 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-[#f95400]/40 disabled:opacity-50"
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : useTextarea ? (
            <textarea
              ref={(el) => {
                inputRef.current = el;
              }}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => void commit()}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              disabled={saving}
              rows={textareaRows}
              // `field-sizing: content` lets supporting browsers grow the
              // textarea to fit content; `rows` is the fallback floor.
              style={{ fieldSizing: 'content' } as React.CSSProperties}
              className={cn(
                'w-full rounded-md border border-[#f95400]/40 bg-[#0a0a0a] px-2 py-1.5 text-xs text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-[#f95400]/40 disabled:opacity-50 resize-y',
                isMono && 'font-mono',
              )}
            />
          ) : (
            <input
              ref={(el) => {
                inputRef.current = el;
              }}
              type={kind === 'number' ? 'number' : 'text'}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => void commit()}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              disabled={saving}
              className={cn(
                'rounded-md border border-[#f95400]/40 bg-[#0a0a0a] px-2 py-1 text-xs text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-[#f95400]/40 disabled:opacity-50',
                isMono && 'font-mono',
              )}
            />
          )}
          {saving && <Loader2 size={12} className="animate-spin text-[#f95400]" />}
        </motion.div>
      ) : (
        <motion.button
          key="display"
          type="button"
          onClick={beginEdit}
          initial={{ opacity: 0, scale: 0.98 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.98 }}
          transition={{ duration: 0.12 }}
          whileHover={{ scale: 1.01 }}
          whileTap={{ scale: 0.98 }}
          className={cn(
            'group relative rounded-md border border-white/[0.06] bg-white/[0.03] px-2 py-1 text-xs text-zinc-200 transition-colors hover:border-[#f95400]/30 hover:bg-white/[0.06]',
            useTextarea
              ? 'flex w-full items-start gap-1.5 text-left'
              : 'inline-flex items-center gap-1.5',
            isMono && 'font-mono',
          )}
          title="Click to edit"
        >
          <span className={cn(useTextarea && 'flex-1 whitespace-pre-wrap break-words')}>
            {displayString(value, kind, placeholder)}
          </span>
          <Pencil
            size={10}
            className={cn(
              'opacity-0 transition-opacity group-hover:opacity-60 text-[#f95400]',
              useTextarea && 'mt-0.5 shrink-0',
            )}
            aria-hidden
          />
        </motion.button>
      )}
    </AnimatePresence>
  );
}
