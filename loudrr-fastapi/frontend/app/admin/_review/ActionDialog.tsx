'use client';

import { AlertTriangle, XCircle } from 'lucide-react';

import { Button } from '@/components/admin/Button';
import { Modal } from '@/components/admin/Modal';

interface ActionDialogProps {
  open: boolean;
  /** Dismiss. Ignored while `busy` (Modal blocks backdrop + Escape too). */
  onClose: () => void;
  title: string;
  description?: string;
  tone?: 'default' | 'danger';
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  /**
   * The last failure, kept on screen until the admin acts on it. A toast was
   * the wrong container: it vanished after four seconds while the dialog
   * stayed open with Confirm still armed, so the natural next move was to
   * fire the same irreversible action again.
   */
  error?: string | null;
  /**
   * The action can no longer succeed — a 409 (someone else decided this row,
   * or the handle is already taken). Confirm is disabled; Cancel is the way
   * out.
   */
  blocked?: boolean;
  /**
   * Overrides the `danger` tone's standard caution line. Pass the truth for
   * the action at hand — a waitlist rejection IS undoable (Reopen), and
   * telling a reviewer otherwise makes them hesitate over the right call.
   */
  warning?: React.ReactNode;
  children?: React.ReactNode;
  onConfirm: () => void | Promise<void>;
  size?: 'sm' | 'md' | 'lg';
}

/**
 * The review queues' confirmation dialog.
 *
 * This is `components/admin/ConfirmDialog` plus the two things a review
 * decision needs and that component has no props for: a persistent in-dialog
 * error, and a Confirm button that can be disabled once the action is known
 * to be impossible. Same Modal, same buttons, same danger banner — so it
 * reads identically to every other confirmation in the panel.
 */
export function ActionDialog({
  open,
  onClose,
  title,
  description,
  tone = 'default',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  busy = false,
  error = null,
  blocked = false,
  warning,
  children,
  onConfirm,
  size = 'md',
}: ActionDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size={size}
      busy={busy}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {blocked ? 'Close' : cancelLabel}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'success'}
            loading={busy}
            disabled={blocked}
            title={blocked ? 'This action can no longer succeed — close and reload' : undefined}
            onClick={() => void onConfirm()}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        {children}

        {tone === 'danger' && !error && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-900/40 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400" aria-hidden />
            <span>
              {warning ?? "This is audit-logged and can't be undone from the panel."}
            </span>
          </div>
        )}

        {error && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-200"
          >
            <XCircle size={14} className="mt-0.5 shrink-0 text-red-400" aria-hidden />
            {/* pre-line: a partial-failure report is one line per row. */}
            <span className="whitespace-pre-line break-words">{error}</span>
          </div>
        )}
      </div>
    </Modal>
  );
}
