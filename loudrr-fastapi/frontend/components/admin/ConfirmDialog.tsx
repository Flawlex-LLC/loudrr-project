'use client';

import { useCallback, useRef, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { Button } from '@/components/admin/Button';
import { Modal } from '@/components/admin/Modal';

interface ConfirmDialogProps {
  open: boolean;
  /** Dismiss (backdrop / Escape / Cancel). Ignored while `busy`. */
  onClose: () => void;
  title: string;
  description?: string;
  /** `danger` turns the confirm button red and adds a warning glyph. */
  tone?: 'default' | 'danger';
  confirmLabel?: string;
  cancelLabel?: string;
  /** Controlled in-flight state. Leave undefined to let the dialog track its own. */
  busy?: boolean;
  /** Extra context above the buttons — the row being acted on, a reason field, … */
  details?: React.ReactNode;
  onConfirm: () => void | Promise<void>;
}

/**
 * The standard "are you sure" for destructive admin actions, built on the
 * focus-trapping Modal.
 *
 * If `onConfirm` returns a promise the dialog manages its own busy state:
 * the confirm button spins, and backdrop/Escape/Cancel are blocked until it
 * settles, so an admin can't fire a ban twice or close the dialog mid-request
 * and never learn whether it landed.
 */
export function ConfirmDialog({
  open,
  onClose,
  title,
  description,
  tone = 'default',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  busy,
  details,
  onConfirm,
}: ConfirmDialogProps) {
  const [selfBusy, setSelfBusy] = useState(false);
  const inFlight = useRef(false);
  const isBusy = busy ?? selfBusy;

  const handleConfirm = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setSelfBusy(true);
    try {
      await onConfirm();
    } finally {
      inFlight.current = false;
      setSelfBusy(false);
    }
  }, [onConfirm]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size="sm"
      busy={isBusy}
      footer={
        <>
          {/* Autofocus hint only applies when `details` contributes no form
              control of its own (Modal prefers real inputs). For a destructive
              action it points at Cancel, so a reflexive Enter is a no-op
              rather than a ban. */}
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={isBusy}
            {...(tone === 'danger' ? { 'data-autofocus': true } : {})}
          >
            {cancelLabel}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            loading={isBusy}
            onClick={() => void handleConfirm()}
            {...(tone === 'danger' ? {} : { 'data-autofocus': true })}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        {tone === 'danger' && (
          <div className="flex items-start gap-2 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-200">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-red-400" aria-hidden />
            <span>This action is audit-logged and can&apos;t be undone from the panel.</span>
          </div>
        )}
        {details}
      </div>
    </Modal>
  );
}
