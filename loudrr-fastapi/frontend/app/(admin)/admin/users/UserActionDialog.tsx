'use client';

/**
 * The one dialog behind every user mutation — grant, revoke, ban, unban and
 * the whitelist flag — shared by the Users list and the user detail page so
 * the two can never drift apart on copy or on what they actually report.
 *
 * What this fixes, specifically:
 *  - every mutation now goes through <ConfirmDialog/>, which blocks a double
 *    fire while the request is in flight;
 *  - a revoke toasts the DEDUCTED amount from the response, not the amount
 *    that was typed (apply_penalty clamps to the balance);
 *  - a grant carries a client-generated request id, so a retried fetch or a
 *    double-click grants once;
 *  - acting on an ADMIN or SUPERADMIN shows a warning in the dialog;
 *  - the copy describes what the backend now does (unban restores the
 *    whitelist flag; a grant does count toward total_credits_earned).
 */

import { useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';

import { adminApi } from '@/lib/api';
import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { Input, Textarea } from '@/components/admin/Input';
import { karma } from '@/lib/admin/format';

export type UserAction = 'grant' | 'revoke' | 'ban' | 'unban' | 'whitelist-on' | 'whitelist-off';

/** The minimum a caller has to know about the target to act on it. */
export interface ActionTarget {
  id: string;
  telegram_id: number | null;
  telegram_username: string;
  x_username: string;
  display_name: string;
  credits: number;
  role: '' | 'admin' | 'superadmin';
  is_banned: boolean;
  is_whitelisted: boolean;
}

/** Display name for a user, in the order an admin can actually recognise. */
export function identityLabel(u: {
  telegram_username?: string;
  display_name?: string;
  x_username?: string;
  telegram_id?: number | null;
}): string {
  if (u.telegram_username) return `@${u.telegram_username}`;
  if (u.display_name) return u.display_name;
  if (u.x_username) return `@${u.x_username.replace(/^@/, '')}`;
  if (u.telegram_id) return `tg:${u.telegram_id}`;
  return 'unknown user';
}

// Amount ceiling mirrors the server's: credits is Numeric(12,4), so anything
// above this is a 422 — catch it in the field instead of after a round-trip.
export const MAX_KARMA_AMOUNT = 99999999.9999;

/**
 * One idempotency token per dialog OPENING — a retried fetch or a double
 * submit of the same dialog is the same request; re-opening it is not.
 * Minted by the caller in the click handler (impure work belongs in an event
 * handler, not in render) and passed in.
 */
export function newRequestId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

interface Props {
  action: UserAction | null;
  target: ActionTarget | null;
  /** Idempotency token for this opening; see newRequestId(). */
  requestId: string;
  onClose: () => void;
  /** Called after a successful mutation so the caller can refetch. */
  onDone: () => void | Promise<void>;
}

const TITLES: Record<UserAction, (name: string) => string> = {
  grant: (n) => `Grant karma to ${n}`,
  revoke: (n) => `Revoke karma from ${n}`,
  ban: (n) => `Ban ${n}?`,
  unban: (n) => `Unban ${n}?`,
  'whitelist-on': (n) => `Whitelist ${n}?`,
  'whitelist-off': (n) => `Remove ${n} from the whitelist?`,
};

const DESCRIPTIONS: Record<UserAction, string> = {
  grant:
    'Audit-logged. Adds to the balance AND to total_credits_earned, so the karma is immediately spendable. Sends the user a Telegram notification.',
  revoke:
    'Audit-logged. Clamps to the current balance — you will be told how much was actually taken, which can be less than you ask for.',
  ban:
    'Audit-logged. Sets is_banned and clears is_whitelisted (the DB forbids both). The previous whitelist value is recorded so an unban can restore it.',
  unban:
    'Audit-logged. Clears is_banned and restores the whitelist flag this ban cleared, so the user can finish onboarding.',
  'whitelist-on':
    'Audit-logged. Grants product access directly, the same flag a waitlist approval sets. Superadmin only.',
  'whitelist-off':
    'Audit-logged. Revokes product access without banning the account. Superadmin only.',
};

/**
 * Mount this with a `key` that changes per opening (action + target id) so the
 * draft state resets by remounting instead of by a reset effect.
 */
export function UserActionDialog({ action, target, requestId, onClose, onDone }: Props) {
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [fieldError, setFieldError] = useState<string | null>(null);

  if (!action || !target) return null;

  const name = identityLabel(target);
  const needsAmount = action === 'grant' || action === 'revoke';
  const isPrivileged = target.role === 'admin' || target.role === 'superadmin';

  async function run() {
    if (!action || !target) return;
    if (needsAmount) {
      const value = Number.parseFloat(amount);
      if (!Number.isFinite(value) || value <= 0) {
        setFieldError('Enter a positive amount.');
        throw new Error('invalid amount');
      }
      if (value > MAX_KARMA_AMOUNT) {
        setFieldError(`Maximum is ${karma(MAX_KARMA_AMOUNT)} karma.`);
        throw new Error('amount too large');
      }
    }

    try {
      if (action === 'grant') {
        const res = await adminApi.grantCredits(
          target.id, Number.parseFloat(amount), note, requestId,
        );
        if (res.duplicate) {
          toast.info('Already granted', {
            description: `This request was already applied — ${name} keeps ${karma(res.credits)}.`,
          });
        } else {
          toast.success(`Granted ${karma(res.granted)} karma`, {
            description: `${name} → new balance ${karma(res.credits)}`,
          });
        }
      } else if (action === 'revoke') {
        const res = await adminApi.revokeCredits(
          target.id, Number.parseFloat(amount), note, requestId,
        );
        if (res.deducted === 0) {
          toast.warning('Nothing was taken', {
            description: `${name} had ${karma(res.credits)} — no karma to deduct.`,
          });
        } else if (res.clamped) {
          // the exact bug this replaces: "Revoked 400" while 355.35 was taken
          toast.success(`Revoked ${karma(res.deducted)} karma`, {
            description: `Clamped from ${karma(res.requested)} — that was the whole balance. ${name} now has ${karma(res.credits)}.`,
          });
        } else {
          toast.success(`Revoked ${karma(res.deducted)} karma`, {
            description: `${name} → new balance ${karma(res.credits)}`,
          });
        }
      } else if (action === 'ban') {
        const res = await adminApi.banUser(target.id, note);
        toast.success(`Banned ${name}`, {
          description: target.is_whitelisted
            ? 'Whitelist cleared — an unban will restore it.'
            : (note || 'No reason recorded.'),
        });
        void res;
      } else if (action === 'unban') {
        const res = await adminApi.unbanUser(target.id);
        toast.success(`Unbanned ${name}`, {
          description: res.is_whitelisted
            ? 'Whitelist restored — the user can onboard again.'
            : 'This user was not whitelisted before the ban; use Whitelist to grant access.',
        });
      } else {
        const value = action === 'whitelist-on';
        await adminApi.setWhitelist(target.id, value);
        toast.success(value ? `Whitelisted ${name}` : `Removed ${name} from the whitelist`);
      }
      onClose();
      await onDone();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Request failed';
      toast.error(`${action.replace('-', ' ')} failed`, {
        description: msg.replace(/^\d+:\s*/, ''),
      });
      throw e; // keep the dialog open so the admin can correct and retry
    }
  }

  return (
    <ConfirmDialog
      open
      onClose={onClose}
      title={TITLES[action](name)}
      description={DESCRIPTIONS[action]}
      tone={action === 'grant' || action === 'unban' || action === 'whitelist-on' ? 'default' : 'danger'}
      confirmLabel={CONFIRM_LABELS[action]}
      onConfirm={run}
      details={
        <div className="space-y-3">
          {isPrivileged && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-900/60 bg-amber-950/30 p-3 text-xs text-amber-200">
              <ShieldAlert size={14} className="mt-0.5 shrink-0" aria-hidden />
              <span>
                This account is a <strong className="uppercase">{target.role}</strong>.
                {action === 'ban' && ' Banning it removes their access to the panel.'}
              </span>
            </div>
          )}

          <dl className="grid grid-cols-[110px_1fr] gap-y-1.5 rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-xs">
            <dt className="text-zinc-500">User</dt>
            <dd className="text-zinc-200">{name}</dd>
            <dt className="text-zinc-500">Telegram id</dt>
            <dd className="font-mono text-zinc-300">{target.telegram_id ?? '—'}</dd>
            <dt className="text-zinc-500">Balance</dt>
            <dd className="font-mono tabular-nums text-zinc-200">{karma(target.credits)}</dd>
            <dt className="text-zinc-500">Status</dt>
            <dd className={target.is_banned ? 'text-red-400' : 'text-zinc-200'}>
              {target.is_banned ? 'banned' : 'active'}
              {target.is_whitelisted ? ' · whitelisted' : ''}
            </dd>
          </dl>

          {needsAmount && (
            <Input
              // `name` is what associates the <label> with the field in the
              // shared Input (it derives htmlFor from id ?? name) — without it
              // the label is decorative and screen readers announce nothing
              name={`${action}-amount`}
              label="Amount (karma)"
              type="number"
              step="0.01"
              min="0.01"
              max={MAX_KARMA_AMOUNT}
              inputMode="decimal"
              placeholder="e.g. 25"
              value={amount}
              autoFocus
              onChange={(e) => { setAmount(e.target.value); setFieldError(null); }}
              error={fieldError ?? undefined}
              hint={
                action === 'revoke'
                  ? `Clamped to the current balance (${karma(target.credits)}).`
                  : `Adds to the balance and to lifetime earned. Max ${karma(MAX_KARMA_AMOUNT)}.`
              }
            />
          )}

          {(action === 'grant' || action === 'revoke' || action === 'ban') && (
            <Textarea
              name={`${action}-note`}
              label={action === 'ban' ? 'Reason (audit log + Telegram message)' : 'Description (audit log)'}
              placeholder={
                action === 'ban'
                  ? 'e.g. botting, fraud, ToS violation…'
                  : 'e.g. promo bonus, partnership reward…'
              }
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              maxLength={500}
            />
          )}
        </div>
      }
    />
  );
}

const CONFIRM_LABELS: Record<UserAction, string> = {
  grant: 'Grant karma',
  revoke: 'Revoke karma',
  ban: 'Ban user',
  unban: 'Unban user',
  'whitelist-on': 'Whitelist',
  'whitelist-off': 'Remove whitelist',
};
