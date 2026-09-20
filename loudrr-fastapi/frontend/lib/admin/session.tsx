'use client';

/**
 * Admin session context — one fetch of `/api/admin/me/` for the whole panel,
 * plus the moderation queue counts that drive the sidebar badges.
 *
 * Before this, every page re-fetched `me()` for its own role check and the
 * layout re-fetched two full pending LISTS on every route change just to read
 * `.length` (which capped the badge at the list limit and went stale the
 * moment an admin approved something in-page).
 */

import {
  cloneElement,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { usePathname } from 'next/navigation';
import { Lock } from 'lucide-react';

import { adminApi, type AdminStats } from '@/lib/api';
import { cn } from '@/lib/utils';

export type AdminRole = 'admin' | 'superadmin' | '';

export interface AdminMe {
  id: string;
  telegram_id: number | null;
  telegram_username: string;
  role: AdminRole;
}

export type QueueCounts = AdminStats['queues'];

const EMPTY_QUEUES: QueueCounts = {
  pending_waitlist: 0,
  pending_x_verifications: 0,
  pending_batches: 0,
};

/**
 * `loading`   — /me/ in flight, render the "Checking access…" gate.
 * `ready`     — authenticated AND role is admin/superadmin.
 * `forbidden` — authenticated but no admin role (or the API said 401/403).
 * `error`     — the call failed for another reason (network, 500); retryable.
 */
export type AdminSessionStatus = 'loading' | 'ready' | 'forbidden' | 'error';

export interface AdminSessionValue {
  me: AdminMe | null;
  isSuperadmin: boolean;
  queues: QueueCounts;
  refreshQueues: () => Promise<void>;
  /** Shell-only extras — pages should only need the four fields above. */
  status: AdminSessionStatus;
  error: string | null;
  reload: () => void;
}

const AdminSessionContext = createContext<AdminSessionValue | null>(null);

export function AdminSessionProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [me, setMe] = useState<AdminMe | null>(null);
  const [status, setStatus] = useState<AdminSessionStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [queues, setQueues] = useState<QueueCounts>(EMPTY_QUEUES);
  const [reloadKey, setReloadKey] = useState(0);

  /** Re-run the identity check (the gate's Retry button). */
  const reload = useCallback(() => {
    // Reset here rather than at the top of the effect: an effect that setStates
    // synchronously costs an extra render pass on every mount.
    setStatus('loading');
    setError(null);
    setReloadKey((k) => k + 1);
  }, []);

  // ---- identity ----
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const identity = await adminApi.me();
        if (cancelled) return;
        if (!identity.role) {
          setMe(null);
          setStatus('forbidden');
          return;
        }
        setMe(identity);
        setStatus('ready');
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : 'Failed to verify admin access';
        setMe(null);
        // adminApiRequest throws "<status>: <detail>" — 401/403 is a real
        // "you're not an admin", anything else is an outage worth retrying.
        setStatus(/^(401|403)\b/.test(msg) ? 'forbidden' : 'error');
        setError(msg);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // ---- queue badges ----
  // A ref-guard so an in-flight refresh can't land after a newer one and
  // resurrect a stale count.
  const queueSeq = useRef(0);

  const refreshQueues = useCallback(async () => {
    const seq = ++queueSeq.current;
    try {
      const next = await adminApi.queues();
      if (seq === queueSeq.current) setQueues(next);
    } catch {
      // Badges are decoration — never surface a toast or block the panel.
    }
  }, []);

  // `refreshQueues` is created with an empty dep list, so it never changes —
  // capturing it once keeps it out of the effect's dependency array below.
  const refreshQueuesRef = useRef(refreshQueues);

  // Refresh on mount and on every route change, but only once authorised
  // (otherwise a non-admin fires a guaranteed 403 alongside the gate).
  useEffect(() => {
    if (status !== 'ready') return;
    void (async () => {
      await refreshQueuesRef.current();
    })();
  }, [status, pathname]);

  const value = useMemo<AdminSessionValue>(
    () => ({
      me,
      isSuperadmin: me?.role === 'superadmin',
      queues,
      refreshQueues,
      status,
      error,
      reload,
    }),
    [me, queues, refreshQueues, status, error, reload],
  );

  return <AdminSessionContext.Provider value={value}>{children}</AdminSessionContext.Provider>;
}

/**
 * Read the shared admin session. Must be called under `<AdminSessionProvider>`
 * (mounted in app/admin/layout.tsx, so every admin page qualifies).
 */
export function useAdminSession(): AdminSessionValue {
  const ctx = useContext(AdminSessionContext);
  if (!ctx) {
    throw new Error('useAdminSession must be used inside <AdminSessionProvider> (app/admin/layout.tsx)');
  }
  return ctx;
}

/** Format a badge count: caps the rendered width at "99+". */
export function badgeCount(n: number): string | null {
  if (!Number.isFinite(n) || n <= 0) return null;
  return n > 99 ? '99+' : String(n);
}

type GatedChild = React.ReactElement<{
  disabled?: boolean;
  className?: string;
  'aria-disabled'?: boolean;
  tabIndex?: number;
}>;

interface RequiresRoleProps {
  role: 'admin' | 'superadmin';
  children: GatedChild;
  /** Overrides the default "Superadmin only" tooltip copy. */
  reason?: string;
  className?: string;
}

/**
 * Render a privileged control, disabled with an explanatory tooltip when the
 * signed-in admin lacks the role.
 *
 * The backend already gates `revoke-credits` and the site-settings PUT on
 * superadmin; the UI used to show those buttons fully enabled and only
 * revealed the truth as a 403 toast after the admin had filled in a form.
 *
 *   <RequiresRole role="superadmin">
 *     <Button variant="danger" onClick={revoke}>Revoke</Button>
 *   </RequiresRole>
 *
 * The child is cloned with `disabled` + `aria-disabled` and wrapped in a
 * pointer-events-blocking span, so it can't be activated by mouse, touch,
 * keyboard or a synthetic click.
 */
export function RequiresRole({ role, children, reason, className }: RequiresRoleProps) {
  const { me } = useAdminSession();
  const allowed = role === 'superadmin' ? me?.role === 'superadmin' : Boolean(me?.role);

  if (allowed) return children;

  const label = reason ?? (role === 'superadmin' ? 'Superadmin only' : 'Admin only');

  return (
    <span className={cn('group/role relative inline-flex cursor-not-allowed', className)} title={label}>
      {/* pointer-events-none makes the disabled child unclickable while the
          wrapper still receives hover, so the tooltip shows. The child's own
          `disabled:` styles do the dimming — don't double it here. */}
      <span className="pointer-events-none inline-flex">
        {cloneElement(children, {
          disabled: true,
          'aria-disabled': true,
          tabIndex: -1,
        })}
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-40 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-white/[0.10] bg-[#1a1a1a] px-2 py-1 text-[11px] font-medium text-zinc-200 opacity-0 shadow-lg shadow-black/50 transition-opacity duration-150 group-hover/role:opacity-100 group-focus-within/role:opacity-100"
      >
        <Lock size={10} className="mr-1 inline-block align-[-1px] text-zinc-400" aria-hidden />
        {label}
      </span>
    </span>
  );
}
