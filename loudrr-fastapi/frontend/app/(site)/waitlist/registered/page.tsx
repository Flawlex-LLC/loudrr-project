'use client';

/**
 * /waitlist/registered — "You're on the waitlist" screen.
 *
 * Shown to people who HAVE applied and are waiting for approval. Displays
 * the waitlist card + referral share buttons.
 *
 * The X username + referral code come from query params (?u=...&ref=...),
 * passed in when the registration form navigates here on success.
 *
 * Note: no auth/redirect logic here by design — this URL always shows the
 * screen so it can be opened and reviewed directly.
 */
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { WaitlistPendingScreen } from '../../app/screens/waitlist-pending';

function RegisteredInner() {
  const params = useSearchParams();
  const xUsername = params.get('u') || undefined;
  const referralCode = params.get('ref') || undefined;
  return <WaitlistPendingScreen xUsername={xUsername} referralCode={referralCode} />;
}

export default function WaitlistRegisteredRoute() {
  // useSearchParams must be inside a Suspense boundary in the App Router.
  return (
    <Suspense fallback={null}>
      <RegisteredInner />
    </Suspense>
  );
}
