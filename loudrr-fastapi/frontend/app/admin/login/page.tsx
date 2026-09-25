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
 * (/setdomain), so the page says so if it can't load.
 */
import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Loader2, ShieldCheck } from 'lucide-react';

import { adminApi } from '@/lib/api';

type TelegramUser = Record<string, string | number>;

function LoginInner() {
  const params = useSearchParams();
  const next = params.get('next');
  const target = next && next.startsWith('/admin') && !next.startsWith('/admin/login') ? next : '/admin';

  const widgetRef = useRef<HTMLDivElement>(null);
  const [bot, setBot] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'signing' | 'error'>('loading');
  const [message, setMessage] = useState<string | null>(null);

  const onAuth = useCallback(async (user: TelegramUser) => {
    setState('signing');
    setMessage(null);
    try {
      await adminApi.telegramLogin(user);
      window.location.replace(target);
    } catch (e) {
      const text = String((e as Error)?.message || '');
      setState('ready');
      setMessage(
        text.startsWith('403')
          ? "This Telegram account isn't a Loudrr admin. Sign in with the account that has admin access."
          : text.startsWith('401')
            ? 'Telegram sign-in failed or took too long. Try again.'
            : "Couldn't reach Loudrr. Check your connection and try again.",
      );
    }
  }, [target]);

  // already signed in? go straight in
  useEffect(() => {
    adminApi.me().then(() => window.location.replace(target)).catch(() => undefined);
  }, [target]);

  useEffect(() => {
    adminApi.authConfig()
      .then((c) => setBot(c.bot_username || null))
      .catch(() => { setState('error'); setMessage("Couldn't reach Loudrr. Reload to try again."); });
  }, []);

  useEffect(() => {
    if (!bot || !widgetRef.current) return;
    (window as unknown as { onLoudrrTelegramAuth: (u: TelegramUser) => void }).onLoudrrTelegramAuth = onAuth;
    const script = document.createElement('script');
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.async = true;
    script.setAttribute('data-telegram-login', bot);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '12');
    script.setAttribute('data-request-access', 'write');
    script.setAttribute('data-onauth', 'onLoudrrTelegramAuth(user)');
    script.onload = () => setState('ready');
    script.onerror = () => {
      setState('error');
      setMessage("Telegram's sign-in button didn't load. Check your connection, or an ad blocker.");
    };
    const host = widgetRef.current;
    host.replaceChildren(script);
    return () => { host.replaceChildren(); };
  }, [bot, onAuth]);

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

        <div className="mt-7 flex min-h-[48px] items-center justify-center">
          {state === 'signing' ? (
            <span className="inline-flex items-center gap-2 text-sm text-zinc-300">
              <Loader2 size={16} className="animate-spin text-[#f95400]" /> Signing you in…
            </span>
          ) : (
            <div ref={widgetRef} aria-label="Log in with Telegram" />
          )}
          {state === 'loading' && !bot && <Loader2 size={18} className="animate-spin text-zinc-500" />}
        </div>

        {message && (
          <p role="alert" className="mt-5 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-left text-sm text-red-200">
            {message}
          </p>
        )}
        <p className="mt-6 text-xs text-zinc-500">
          Sessions last 12 hours. Every action is checked against your admin role and written to the audit log.
        </p>
      </div>
    </div>
  );
}

export default function AdminLoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  );
}
