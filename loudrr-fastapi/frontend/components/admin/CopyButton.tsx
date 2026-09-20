'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';

interface CopyButtonProps {
  /** The exact text put on the clipboard. */
  value: string;
  /** Visible text next to the icon. Omit for an icon-only button. */
  label?: string;
  className?: string;
}

/**
 * Copy a value (uuid, telegram id, X handle, tweet link) to the clipboard.
 *
 * The panel had no copy affordance at all, so every "grab this user's id"
 * meant a manual selection out of a truncated monospace cell. Falls back to
 * the legacy execCommand path because `navigator.clipboard` is undefined on
 * non-secure origins — which includes hitting the panel over plain http from
 * a phone on the LAN.
 */
export function CopyButton({ value, label, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const copy = useCallback(async () => {
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
        ok = true;
      }
    } catch {
      ok = false;
    }
    if (!ok) {
      try {
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand('copy');
        document.body.removeChild(ta);
      } catch {
        ok = false;
      }
    }
    if (!ok) return;

    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 1400);
  }, [value]);

  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={copied ? 'Copied' : `Copy${label ? ` ${label}` : ''}`}
      title={copied ? 'Copied' : `Copy${label ? ` ${label}` : ''}`}
      className={cn(
        'inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-md border px-2 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f95400]/40',
        copied
          ? 'border-emerald-700/60 bg-emerald-950/50 text-emerald-300'
          : 'border-white/[0.08] bg-white/[0.03] text-zinc-400 hover:border-white/[0.16] hover:bg-white/[0.07] hover:text-white',
        className,
      )}
    >
      {copied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
      {/* Reserve the label's width for both states so the row doesn't reflow
          when the text swaps to "Copied". */}
      {label !== undefined ? <span>{copied ? 'Copied' : label}</span> : null}
      <span aria-live="polite" className="sr-only">
        {copied ? 'Copied to clipboard' : ''}
      </span>
    </button>
  );
}
