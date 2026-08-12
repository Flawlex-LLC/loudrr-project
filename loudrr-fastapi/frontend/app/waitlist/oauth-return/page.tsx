'use client';

/**
 * /waitlist/oauth-return — Landing page after the X OAuth callback.
 *
 * The backend's /api/auth/x/callback/waitlist/ handler 302-redirects the user
 * to this route with either:
 *   ?proof=<signed itsdangerous token>   on success
 *   ?error=<code>                        on failure (denied|invalid|expired|token|profile)
 *
 * We stash the value in sessionStorage under a well-known key and bounce the
 * user back to /waitlist, where the WaitlistRegistrationScreen picks it up on
 * mount and advances past step 1 (Connect X) to step 2 (region).
 *
 * sessionStorage is the right lifetime here: it survives the tab reload from
 * X → backend → here → /waitlist, and it clears when the user closes the
 * mini-app. If the user closes without submitting, they'll re-Connect X on
 * their next visit (which is what we want — the proof is only 10 min valid).
 */
import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

function OAuthReturnInner() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const proof = params.get('proof');
    const error = params.get('error');
    try {
      if (proof) {
        sessionStorage.setItem('x_oauth_proof', proof);
        // Wall-clock issuance time so /waitlist can enforce an 8-minute
        // client-side freshness window. The proof itself has a 10-minute
        // server-side max_age; the tighter client bound is defense-in-depth
        // against marching the user all the way to step 3 only to 400 at
        // submit because the proof aged out in a background tab.
        sessionStorage.setItem('x_oauth_proof_iat', String(Date.now()));
      } else if (error) {
        sessionStorage.setItem('x_oauth_error', error);
      }
    } catch {
      // sessionStorage may be blocked in some in-app browsers — fall through.
    }
    router.replace('/waitlist');
  }, [params, router]);

  return (
    <div
      className="min-h-screen bg-black flex items-center justify-center p-6"
      style={{ color: 'rgba(255,255,255,0.6)' }}
    >
      <div className="text-center">
        <div className="w-8 h-8 border-2 border-white/20 border-t-[#f95400] rounded-full animate-spin mx-auto mb-4" />
        <p className="text-sm">Returning to Loudrr…</p>
      </div>
    </div>
  );
}

export default function OAuthReturnRoute() {
  return (
    <Suspense fallback={null}>
      <OAuthReturnInner />
    </Suspense>
  );
}
