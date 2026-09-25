'use client';

import { MessageSquare, Lock } from 'lucide-react';

import { Textarea } from '@/components/admin/Input';
import { cn } from '@/lib/utils';

export const PUBLIC_REASON_MAX = 300;
export const INTERNAL_NOTE_MAX = 1000;

interface ReasonFieldsProps {
  /** The public reason — sent to the person, verbatim. */
  reason: string;
  onReasonChange: (value: string) => void;
  /** The internal note — audit log only. */
  note: string;
  onNoteChange: (value: string) => void;
  /** One-tap reasons for the cases that come up every day. */
  presets?: string[];
  /** Renders the exact message the person will receive, for `reason`. */
  previewFor: (reason: string) => string;
  disabled?: boolean;
  idPrefix: string;
  /** Who the public reason is addressed to — "applicant", "user", … */
  subject?: string;
}

/**
 * The rejection form for both review queues: one field the applicant reads,
 * one the team reads, and no way to confuse them.
 *
 * Both pages used to show a SINGLE box labelled "Rejection reason (optional,
 * internal)" with the hint "Visible only in audit logs; the applicant doesn't
 * see it" and the placeholder "e.g. bot, profile doesn't match niche, low
 * signal account…". That string was passed to the outbox and DM'd verbatim —
 * so an admin taking the placeholder's advice sent an applicant the word
 * "bot". The preview below is the fix that makes the split self-evident:
 * whatever is typed in the public box is shown in the message it becomes.
 */
export function ReasonFields({
  reason,
  onReasonChange,
  note,
  onNoteChange,
  presets = [],
  previewFor,
  disabled,
  idPrefix,
  subject = 'applicant',
}: ReasonFieldsProps) {
  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1.5 flex items-center gap-1.5">
          <MessageSquare size={12} className="text-[#f95400]" aria-hidden />
          <span className="text-xs font-semibold text-zinc-200">
            Reason — sent to them
          </span>
        </div>

        {presets.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {presets.map((preset) => (
              <button
                key={preset}
                type="button"
                disabled={disabled}
                onClick={() => onReasonChange(preset)}
                aria-pressed={reason === preset}
                className={cn(
                  'rounded-full border px-2.5 py-1 text-[11px] transition-colors disabled:opacity-50',
                  reason === preset
                    ? 'border-[#f95400]/50 bg-[#f95400]/15 text-white'
                    : 'border-white/[0.08] bg-white/[0.03] text-zinc-400 hover:border-white/[0.16] hover:text-white',
                )}
              >
                {preset}
              </button>
            ))}
          </div>
        )}

        <Textarea
          id={`${idPrefix}-reason`}
          value={reason}
          onChange={(e) => onReasonChange(e.target.value.slice(0, PUBLIC_REASON_MAX))}
          maxLength={PUBLIC_REASON_MAX}
          rows={2}
          disabled={disabled}
          placeholder="Optional. Leave empty to send no reason at all."
        />
        <div className="mt-1 flex items-start justify-between gap-3">
          <p className="text-[11px] text-zinc-500">
            Written for the {subject} — it goes out in their Telegram message.
          </p>
          <Counter value={reason.length} max={PUBLIC_REASON_MAX} />
        </div>

        <div className="mt-2 rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-2.5">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            They will receive
          </p>
          <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-300">
            {previewFor(reason)}
          </p>
          <p className="mt-1.5 text-[10px] text-zinc-600">
            Default template — an edited TG_MSG_* site setting overrides it.
          </p>
        </div>
      </div>

      <div>
        <div className="mb-1.5 flex items-center gap-1.5">
          <Lock size={12} className="text-zinc-500" aria-hidden />
          <span className="text-xs font-semibold text-zinc-200">
            Internal note — team only
          </span>
        </div>
        <Textarea
          id={`${idPrefix}-note`}
          value={note}
          onChange={(e) => onNoteChange(e.target.value.slice(0, INTERNAL_NOTE_MAX))}
          maxLength={INTERNAL_NOTE_MAX}
          rows={2}
          disabled={disabled}
          placeholder="e.g. bot farm — 4-day-old account, 0 original posts, same IP as #905012"
        />
        <div className="mt-1 flex items-start justify-between gap-3">
          <p className="text-[11px] text-zinc-500">
            Recorded in the audit log. Never sent to anyone.
          </p>
          <Counter value={note.length} max={INTERNAL_NOTE_MAX} />
        </div>
      </div>
    </div>
  );
}

function Counter({ value, max }: { value: number; max: number }) {
  const near = value > max * 0.9;
  return (
    <span
      className={cn(
        'shrink-0 text-[11px] tabular-nums',
        value >= max ? 'text-red-400' : near ? 'text-amber-400' : 'text-zinc-600',
      )}
    >
      {value}/{max}
    </span>
  );
}

/** The default TG_MSG_WAITLIST_REJECTED body, rendered client-side. */
export function waitlistRejectionPreview(xUsername: string, reason: string): string {
  const handle = (xUsername || '').replace(/^@/, '');
  const namePart = handle ? `, @${handle}` : '';
  // `{reason_part}` — empty reason renders nothing, so the message can't end
  // on a dangling "Reason: "
  const reasonPart = reason.trim() ? ` Reason: ${reason.trim()}` : '';
  return `Your Loudrr waitlist application${namePart} was not approved at this time.${reasonPart}`;
}

/** The default TG_MSG_X_VERIFICATION_REJECTED body. */
export function xVerificationRejectionPreview(
  submitted: string,
  claimed: string,
  reason: string,
): string {
  const notePart = reason.trim() ? ` Note: ${reason.trim()}` : '';
  return (
    `Your X verification request was rejected. ` +
    `Submitted: @${(submitted || '').replace(/^@/, '')}, ` +
    `Claimed: @${(claimed || '').replace(/^@/, '')}.${notePart}`
  );
}
