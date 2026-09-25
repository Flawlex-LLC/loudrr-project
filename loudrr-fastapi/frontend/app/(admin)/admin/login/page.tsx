'use client';

/**
 * /admin/login — sign in to the admin website with Telegram.
 *
 * Telegram's Login Widget (https://core.telegram.org/widgets/login) asks the
 * admin to confirm in their Telegram app, then hands this page a payload
 * signed with the bot's token. The backend verifies it, checks the account
 * has an admin role, and sets a 12-hour session cookie. No passwords.
 *
 * The widget only works on the domain registered for the bot in BotFather
 * (/setdomain). The card renders on the server, so the page is never blank
 * while telegram.org loads; if the widget is slow or fails there's a retry.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw, ShieldCheck } from 'lucide-react';

import { adminApi } from '@/lib/api';

type TelegramUser = Record<string, string | number>;
type Phase = 'loading' | 'ready' | 'signing' | 'error';

const WIDGET_TIMEOUT_MS = 15_000;

/** Where to go after sign-in: the ?next= admin page, never another site. */
function nextTarget(): string {
  const next = new URLSearchParams(window.location.search).get('next');
  return next && next.startsWith('/admin') && !next.startsWith('/admin/login') ? next : '/admin';
}

function signInError(e: unknown): string {
  const text = String((e as Error)?.message || '');
  if (text.startsWith('403')) {
    return "This Telegram account isn't a Loudrr admin. Sign in with the account that has admin access.";
  }
  if (text.startsWith('401')) return 'Telegram sign-in failed or took too long. Try again.';
  if (text.startsWith('429')) return 'Too many sign-in attempts. Wait a minute, then try again.';
  return "Couldn't reach Loudrr. Check your connection and try again.";
}

export default function AdminLoginPage() {
  const widgetRef = useRef<HTMLDivElement>(null);
  const [bot, setBot] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>('loading');
  const [message, setMessage] = useState<string | null>(null);
  // bumping this re-runs the config fetch and re-injects the widget
  const [attempt, setAttempt] = useState(0);

  const onAuth = useCallback(async (user: TelegramUser) => {
    setPhase('signing');
    setMessage(null);
    try {
      await adminApi.telegramLogin(user);
      window.location.replace(nextTarget());
    } catch (e) {
      setPhase('ready');
      setMessage(signInError(e));
    }
  }, []);

  // already signed in? go straight in
  useEffect(() => {
    adminApi.me().then(() => window.location.replace(nextTarget())).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (bot) return;
    adminApi.authConfig()
      .then((c) => {
        if (c.bot_username) {
          setBot(c.bot_username);
        } else {
          setPhase('error');
          setMessage('Telegram sign-in isn’t configured on the server yet.');
        }
      })
      .catch(() => {
        setPhase('error');
        setMessage("Couldn't reach Loudrr. Check your connection and try again.");
      });
  }, [bot, attempt]);

  useEffect(() => {
    const host = widgetRef.current;
    if (!bot || !host) return;
    let settled = false;
    (window as unknown as { onLoudrrTelegramAuth: (u: TelegramUser) => void }).onLoudrrTelegramAuth = onAuth;
    const script = document.createElement('script');
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.async = true;
    script.setAttribute('data-telegram-login', bot);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '12');
    script.setAttribute('data-request-access', 'write');
    script.setAttribute('data-onauth', 'onLoudrrTelegramAuth(user)');
    // The script adds Telegram's iframe, and the button works once that
    // iframe's own script runs: it then posts {"event":"ready"}. The
    // iframe's load event is only a fallback, because it waits for every
    // font and stylesheet inside the frame and telegram.org can stall on
    // any of them. The first signal wins and can't undo "Signing you in…".
    const shown = () => {
      if (settled) return;
      settled = true;
      window.removeEventListener('message', onFrameMessage);
      setPhase((p) => (p === 'signing' ? p : 'ready'));
      setMessage(null);
    };
    function onFrameMessage(e: MessageEvent) {
      if (e.origin === 'https://oauth.telegram.org') shown();
    }
    window.addEventListener('message', onFrameMessage);
    script.onload = () => {
      const frame = host.querySelector('iframe');
      if (frame) frame.addEventListener('load', shown, { once: true });
      else shown();
    };
    script.onerror = () => {
      settled = true;
      setPhase('error');
      setMessage("Telegram's sign-in button didn't load. Check your connection or ad blocker, then try again.");
    };
    const timer = window.setTimeout(() => {
      if (settled) return;
      setPhase('error');
      setMessage('Telegram is taking too long to load the sign-in button. Try again.');
    }, WIDGET_TIMEOUT_MS);
    host.replaceChildren(script);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('message', onFrameMessage);
      host.replaceChildren();
    };
  }, [bot, attempt, onAuth]);

  const retry = () => {
    setMessage(null);
    setPhase('loading');
    setAttempt((n) => n + 1);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0a0a0a] p-6 text-white">
      <div className="w-full max-w-sm rounded-2xl border border-white/[0.08] bg-[#0f0f0f] p-8 text-center">
        <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-xl bg-[#f95400]/15">
          <ShieldCheck size={22} className="text-[#f95400]" />
        </div>
        <h1 className="font-syne text-2xl font-bold tracking-tight">Loudrr admin</h1>
        <p className="mt-2 text-sm text-zinc-400">
          Sign in with the Telegram account that has admin access. Telegram will ask you to confirm in the app.
        </p>

        <div className="mt-7 flex min-h-[48px] flex-col items-center justify-center gap-3">
          {/* stays mounted: Telegram's script puts its button in here */}
          <div ref={widgetRef} aria-label="Log in with Telegram" className={phase === 'signing' ? 'hidden' : undefined} />
          {phase === 'loading' && (
            <span className="inline-flex items-center gap-2 text-sm text-zinc-400">
              <Loader2 size={16} className="animate-spin text-zinc-500" /> Loading Telegram sign-in…
            </span>
          )}
          {phase === 'signing' && (
            <span className="inline-flex items-center gap-2 text-sm text-zinc-300">
              <Loader2 size={16} className="animate-spin text-[#f95400]" /> Signing you in…
            </span>
          )}
        </div>

        {message && (
          <div role="alert" className="mt-5 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-left text-sm text-red-200">
            <p>{message}</p>
            {phase === 'error' && (
              <button
                type="button"
                onClick={retry}
                className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-red-800/60 px-2.5 py-1 text-xs font-medium text-red-100 hover:bg-red-900/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#f95400]"
              >
                <RefreshCw size={12} /> Try again
              </button>
            )}
          </div>
        )}
        <p className="mt-6 text-xs text-zinc-500">
          Sessions last 12 hours. Every action is checked against your admin role and written to the audit log.
        </p>
      </div>
    </div>
  );
}
