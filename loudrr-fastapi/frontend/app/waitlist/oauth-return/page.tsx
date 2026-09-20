'use client';

/**
 * /waitlist/oauth-return — where the SYSTEM browser lands after X OAuth.
 *
 * The backend's /api/auth/x/callback/waitlist/ handler 302s here with either
 *   #confirm=<token>    on success (fragment: browsers never send it to a server)
 *   ?error=<code>       on failure (denied|invalid|expired|token|profile|internal)
 *
 * The confirm step exists because the X authorize link can be forwarded:
 * someone can start "Connect X" in THEIR Telegram and send the link to you, and
 * whoever authorizes gets bound to whoever started it. So this page — the only
 * place the person who just authorized is present — names the Telegram account
 * that will receive the connection and asks them to confirm it. Nothing reaches
 * the mini-app until they do.
 *
 * The proof itself never passes through here: the backend hands it to the
 * applicant's mini-app server-side once confirmed. This tab has no Telegram
 * session, so it never shows the sign-up form.
 */
import { Suspense, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';

const BOT_APP_URL = 'https://t.me/loudrr_bot/app';

const ERROR_COPY: Record<string, string> = {
  denied: 'You cancelled the X authorization.',
  expired: 'That X sign-in link expired.',
  invalid: 'X returned an invalid response.',
  token: "We couldn't complete the handshake with X.",
  profile: "We couldn't read your X profile.",
  internal: 'Something went wrong on our side.',
};

const LINK_GONE = 'This confirmation link is invalid, already used or expired.';

type Phase = 'loading' | 'confirm' | 'confirmed' | 'cancelled' | 'gone' | 'error' | 'plain';

function Shell({ title, body, children }: { title: string; body: string; children?: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-6">
      <div className="text-center max-w-sm w-full">
        <img src="/loudrr-icon.png" alt="Loudrr" className="w-16 h-16 mx-auto mb-6" />
        <h1 className="text-2xl font-bold text-white mb-2">{title}</h1>
        <p className="text-sm mb-8" style={{ color: 'rgba(255,255,255,0.6)' }}>{body}</p>
        {children}
      </div>
    </div>
  );
}

function OpenTelegramButton({ label = 'Open Loudrr in Telegram' }: { label?: string }) {
  return (
    <a
      href={BOT_APP_URL}
      className="inline-flex items-center justify-center h-12 px-6 rounded-2xl text-sm font-semibold text-white"
      style={{
        background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.25) 0%, rgba(255, 140, 66, 0.18) 100%)',
        border: '1px solid rgba(249, 84, 0, 0.45)',
      }}
    >
      {label}
    </a>
  );
}

function OAuthReturnInner() {
  const router = useRouter();
  const params = useSearchParams();
  const error = params.get('error');

  const [token, setToken] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>(error ? 'error' : 'loading');
  const [link, setLink] = useState<{ x_username: string; telegram_label: string } | null>(null);
  const [busy, setBusy] = useState<'confirm' | 'cancel' | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  // Read the one-time token out of the fragment and strip it from the address
  // bar immediately, so it can't be shoulder-surfed, screenshotted or restored
  // from history while it is still live.
  useEffect(() => {
    if (error) return;
    const hash = window.location.hash || '';
    const found = new URLSearchParams(hash.replace(/^#/, '')).get('confirm');
    if (!found) {
      setPhase('plain');
      return;
    }
    setToken(found);
    try {
      window.history.replaceState(window.history.state, '', window.location.pathname);
    } catch {
      /* a blocked replaceState must not stop the confirmation */
    }
  }, [error]);

  // If this somehow opens inside the Telegram WebView there is no confirming to
  // do here — the registration screen owns that flow.
  useEffect(() => {
    const inTelegram = !!(window as unknown as { Telegram?: { WebApp?: { initData?: string } } })
      ?.Telegram?.WebApp?.initData;
    if (inTelegram && !token) router.replace('/waitlist');
  }, [router, token]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const info = await api.getWaitlistXOAuthConfirmation(token);
        if (cancelled) return;
        setLink({ x_username: info.x_username, telegram_label: info.telegram_label });
        setPhase('confirm');
      } catch (e) {
        if (cancelled) return;
        const msg = (e as Error).message || '';
        setPhase(/40[34]/.test(msg) ? 'gone' : 'error');
        setFailure(/40[34]/.test(msg) ? null : "We couldn't load this confirmation. Check your connection and reload.");
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  const decide = useCallback(async (decision: 'confirm' | 'cancel') => {
    if (!token || busy) return;
    setBusy(decision);
    setFailure(null);
    try {
      const res = await api.decideWaitlistXOAuthConfirmation(token, decision);
      setPhase(res.status === 'confirmed' ? 'confirmed' : 'cancelled');
    } catch (e) {
      const msg = (e as Error).message || '';
      if (/40[34]/.test(msg)) setPhase('gone');
      else setFailure("That didn't go through. Check your connection and try again.");
    } finally {
      setBusy(null);
    }
  }, [token, busy]);

  if (phase === 'loading') {
    return <Shell title="One moment" body="Checking what you just authorized on X…" />;
  }

  if (phase === 'confirm' && link) {
    return (
      <Shell
        title="Confirm this connection"
        body="You authorized Loudrr on X. Check the two accounts below before you continue."
      >
        <div className="text-left rounded-2xl p-4 mb-5" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
          <div className="flex items-center gap-3 mb-4">
            <img
              src={`https://unavatar.io/x/${link.x_username}?fallback=false`}
              alt=""
              className="w-10 h-10 rounded-full bg-white/10 object-cover"
              onError={(ev) => { (ev.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }}
            />
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-wide" style={{ color: 'rgba(255,255,255,0.4)' }}>X account</div>
              <div className="text-sm font-semibold text-white truncate">@{link.x_username}</div>
            </div>
          </div>
          <div className="text-center text-xs mb-3" style={{ color: 'rgba(255,255,255,0.35)' }}>will be connected to</div>
          <div>
            <div className="text-[11px] uppercase tracking-wide" style={{ color: 'rgba(255,255,255,0.4)' }}>Telegram account</div>
            <div className="text-sm font-semibold text-white break-words">{link.telegram_label}</div>
          </div>
        </div>

        <p className="text-xs mb-6 text-left" style={{ color: 'rgba(255,255,255,0.55)' }}>
          Only continue if that Telegram account is yours. If someone sent you this link, press Cancel —
          confirming would join the waitlist as <span className="text-white">@{link.x_username}</span> on their Telegram.
        </p>

        {failure && (
          <p className="text-xs mb-4 text-left" style={{ color: '#fca5a5' }}>{failure}</p>
        )}

        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={() => decide('confirm')}
            disabled={!!busy}
            className="h-12 px-6 rounded-2xl text-sm font-semibold text-white disabled:opacity-60"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.25) 0%, rgba(255, 140, 66, 0.18) 100%)',
              border: '1px solid rgba(249, 84, 0, 0.45)',
            }}
          >
            {busy === 'confirm' ? 'Connecting…' : `Yes, connect @${link.x_username}`}
          </button>
          <button
            type="button"
            onClick={() => decide('cancel')}
            disabled={!!busy}
            className="h-11 px-6 rounded-2xl text-sm font-medium disabled:opacity-60"
            style={{ color: 'rgba(255,255,255,0.75)', border: '1px solid rgba(255,255,255,0.14)' }}
          >
            {busy === 'cancel' ? 'Cancelling…' : "Cancel — this wasn't me"}
          </button>
        </div>
      </Shell>
    );
  }

  if (phase === 'confirmed') {
    return (
      <Shell title="X connected" body="Go back to Loudrr in Telegram to finish joining the waitlist.">
        <OpenTelegramButton />
        <p className="text-xs mt-4" style={{ color: 'rgba(255,255,255,0.35)' }}>You can close this tab.</p>
      </Shell>
    );
  }

  if (phase === 'cancelled') {
    return (
      <Shell
        title="Nothing was connected"
        body="We cancelled that request. Your X account wasn't linked to anyone."
      >
        <p className="text-xs" style={{ color: 'rgba(255,255,255,0.35)' }}>You can close this tab.</p>
      </Shell>
    );
  }

  if (phase === 'gone') {
    return (
      <Shell
        title="This link has expired"
        body={`${LINK_GONE} Open Loudrr in Telegram and tap Connect X to start again.`}
      >
        <OpenTelegramButton />
      </Shell>
    );
  }

  if (phase === 'error') {
    return (
      <Shell
        title="X wasn't connected"
        body={`${(error && ERROR_COPY[error]) || failure || 'Something went wrong.'} Go back to Loudrr in Telegram and tap Connect X to try again.`}
      >
        <OpenTelegramButton />
      </Shell>
    );
  }

  return (
    <Shell title="Return to Telegram" body="Go back to Loudrr in Telegram to continue.">
      <OpenTelegramButton />
      <p className="text-xs mt-4" style={{ color: 'rgba(255,255,255,0.35)' }}>You can close this tab.</p>
    </Shell>
  );
}

export default function OAuthReturnRoute() {
  return (
    <Suspense fallback={null}>
      <OAuthReturnInner />
    </Suspense>
  );
}
