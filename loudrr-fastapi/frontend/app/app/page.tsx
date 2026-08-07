'use client';

/**
 * /app — Entry dispatcher.
 *
 * Telegram deep links point here. This route loads the user's state once,
 * then redirects to the correct sub-route:
 *   - approved users        -> /app/home
 *   - everyone else         -> /waitlist
 *
 * The actual screens live in /app/home and /waitlist.
 */
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { initTelegramWebApp, getTelegramWebApp } from '@/lib/telegram';
import { PixelLoader } from './components/leaf';

export default function AppDispatcher() {
  const router = useRouter();

  useEffect(() => {
    initTelegramWebApp();

    (async () => {
      const tg = getTelegramWebApp();
      const host = typeof window !== 'undefined' ? window.location.hostname : '';
      const isDev = host === 'localhost' || host.startsWith('dev-app.');

      // Outside Telegram (and not dev) — the waitlist route shows the
      // "Open in Telegram" screen, so route there.
      if (!tg?.initData && !isDev) {
        router.replace('/waitlist');
        return;
      }

      // Approved users have an account; everyone else goes to the waitlist.
      try {
        await api.getUser();
        router.replace('/app/home');
      } catch {
        router.replace('/waitlist');
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Brief loader while the dispatcher decides where to send the user.
  return <PixelLoader />;
}
