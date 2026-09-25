'use client';

/**
 * /waitlist — Waitlist registration form.
 *
 * Shown to people who have NOT applied yet. On mount it checks the user's
 * waitlist status:
 *   - already approved   -> redirect to /app/home
 *   - already waitlisted -> redirect to /waitlist/registered
 *   - rejected           -> "not approved" screen (with the admin's reason)
 *   - not registered     -> show the registration form
 *
 * In design mode (NEXT_PUBLIC_DESIGN_MODE) the status check is skipped so
 * the form is always visible for design review.
 *
 * On successful submission the form navigates to /waitlist/registered.
 */
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { DESIGN_MODE } from '@/lib/mockData';
import { PixelLoader } from '../app/components/leaf';
import { WaitlistRegistrationScreen } from '../app/screens/waitlist-registration';

export default function WaitlistRoute() {
  const router = useRouter();
  // In design mode, skip the status check and show the form immediately.
  const [checking, setChecking] = useState(!DESIGN_MODE);
  const [rejection, setRejection] = useState<{ x_username?: string; reason?: string } | null>(null);

  useEffect(() => {
    if (DESIGN_MODE) return; // design review: always show the form

    let cancelled = false;
    (async () => {
      try {
        const status = await api.checkWaitlistStatus();
        if (cancelled) return;
        if (status.status === 'approved') {
          router.replace('/app/home');
          return;
        }
        if (status.status === 'waitlisted') {
          const params = new URLSearchParams();
          if (status.x_username) params.set('u', status.x_username);
          if (status.referral_code) params.set('ref', status.referral_code);
          const qs = params.toString();
          router.replace(`/waitlist/registered${qs ? `?${qs}` : ''}`);
          return;
        }
        if (status.status === 'rejected') {
          setRejection({ x_username: status.x_username, reason: status.reason });
          setChecking(false);
          return;
        }
        // not_registered — show the form
        setChecking(false);
      } catch {
        // status check failed — fail open: show the form
        if (!cancelled) setChecking(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (checking) {
    return <PixelLoader />;
  }

  if (rejection) {
    return (
      <div className="min-h-screen bg-black flex flex-col items-center justify-center p-6 text-center">
        <img src="/loudrr-icon.png" alt="Loudrr" className="w-20 h-20 mb-6" />
        <h1 className="text-2xl font-bold text-white mb-2">Application not approved</h1>
        <p className="text-gray-400 max-w-sm mb-3">
          {rejection.x_username ? `@${rejection.x_username}'s` : 'Your'} application to Loudrr wasn't approved this time.
        </p>
        {rejection.reason && (
          <p className="text-sm text-gray-500 max-w-sm">Reason: {rejection.reason}</p>
        )}
      </div>
    );
  }

  return (
    <WaitlistRegistrationScreen
      onSuccess={(data) => {
        // carry the X username + referral code to the registered screen
        const params = new URLSearchParams();
        if (data.x_username) params.set('u', data.x_username);
        if (data.referral_code) params.set('ref', data.referral_code);
        const qs = params.toString();
        router.push(`/waitlist/registered${qs ? `?${qs}` : ''}`);
      }}
    />
  );
}
