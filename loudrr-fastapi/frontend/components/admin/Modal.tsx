'use client';

import { useCallback, useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: 'sm' | 'md' | 'lg';
  /**
   * An action is in flight. Blocks backdrop-click and Escape dismissal so an
   * admin can't close the dialog mid-request and lose sight of the outcome
   * (the close button stays visible but disabled).
   */
  busy?: boolean;
}

/** Everything that can take focus inside the dialog, in DOM order. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

/** Re-scope the focusable selector list under an ancestor (a bare prefix
 *  would only bind to the first clause of a comma-separated selector). */
function within(prefix: string): string {
  return FOCUSABLE.split(', ')
    .map((sel) => `${prefix} ${sel}`)
    .join(', ');
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  busy = false,
}: ModalProps) {
  const ref = useRef<HTMLDivElement>(null);
  // Element that had focus when the dialog opened — focus goes back there on
  // close, so the admin's keyboard position in the table isn't lost.
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // Live refs so the key/focus effects depend only on `open`. Pages pass
  // inline `onClose={() => …}` arrows; including them in the deps would tear
  // down and re-run the focus effect on every render and keep stealing focus.
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);
  useEffect(() => {
    onCloseRef.current = onClose;
    busyRef.current = busy;
  });

  const titleId = useId();
  const descId = useId();

  const requestClose = useCallback(() => {
    if (busyRef.current) return;
    onCloseRef.current();
  }, []);

  // Open: lock the page, remember the trigger, move focus to the first FORM
  // control. Close/unmount: restore everything.
  useEffect(() => {
    if (!open) return;

    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    // rAF (not a 50ms timer) so focus lands on the first paint — the old
    // setTimeout raced with mount animations and grabbed the header X.
    const raf = requestAnimationFrame(() => {
      const root = ref.current;
      if (!root) return;
      // Priority: explicit opt-in > first real form control > first focusable.
      // The header close button is deliberately last: landing there meant a
      // blind admin heard "Close" instead of the field they came to fill in.
      const target =
        root.querySelector<HTMLElement>('[data-autofocus]') ??
        root.querySelector<HTMLElement>(
          'input:not([disabled]):not([type="hidden"]), textarea:not([disabled]), select:not([disabled])',
        ) ??
        root.querySelector<HTMLElement>(within('[data-modal-body]')) ??
        root.querySelector<HTMLElement>(within('[data-modal-footer]')) ??
        root;
      target.focus();
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
        try {
          target.select();
        } catch {
          /* number inputs in some engines reject .select() */
        }
      }
    });

    return () => {
      cancelAnimationFrame(raf);
      document.body.style.overflow = previousOverflow;
      const restore = restoreFocusRef.current;
      restoreFocusRef.current = null;
      // The trigger may have unmounted (row deleted by the action) — only
      // restore if it's still in the document.
      if (restore && document.contains(restore)) restore.focus();
    };
  }, [open]);

  // Escape to dismiss + a Tab trap so focus can't wander into the page behind
  // the backdrop (where clicks do nothing and the admin looks stuck).
  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (busyRef.current) return;
        e.preventDefault();
        requestClose();
        return;
      }
      if (e.key !== 'Tab') return;

      const root = ref.current;
      if (!root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (items.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;

      if (!active || !root.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, requestClose]);

  if (!open) return null;

  const sizeClass = size === 'sm' ? 'max-w-sm' : size === 'lg' ? 'max-w-2xl' : 'max-w-md';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm animate-in fade-in duration-150"
      onClick={(e) => {
        if (e.target === e.currentTarget) requestClose();
      }}
    >
      <div
        ref={ref}
        tabIndex={-1}
        className={cn(
          'my-auto w-full rounded-2xl border border-white/[0.08] bg-[#111] shadow-2xl shadow-black/40 focus:outline-none',
          sizeClass,
        )}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
      >
        <div className="flex items-start justify-between border-b border-white/[0.06] px-6 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="text-base font-semibold text-white">
              {title}
            </h2>
            {description && (
              <p id={descId} className="mt-0.5 text-sm text-zinc-400">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={requestClose}
            disabled={busy}
            className="ml-4 shrink-0 rounded-md p-1 text-zinc-400 transition-colors hover:bg-white/[0.04] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f95400]/40 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        <div data-modal-body className="px-6 py-4">
          {children}
        </div>
        {footer && (
          <div
            data-modal-footer
            className="flex flex-wrap justify-end gap-2 border-t border-white/[0.06] px-6 py-3"
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
