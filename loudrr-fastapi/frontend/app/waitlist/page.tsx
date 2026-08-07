'use client';

/**
 * /waitlist — Waitlist registration form.
 *
 * Shown to people who have NOT applied yet. On mount it checks the user's
 * waitlist status:
 *   - already approved   -> redirect to /app/home
 *   - already waitlisted -> redirect to /waitlist/registered
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
