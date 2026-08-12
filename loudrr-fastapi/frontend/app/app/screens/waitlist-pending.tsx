'use client';

import { useState, useEffect } from 'react';
import { hapticFeedback, openLink } from '@/lib/telegram';
import { api, type WaitlistEnrichment } from '@/lib/api';
import { ClipboardIcon, CheckIconFill, XLogoIcon, TelegramIcon } from '../icons';

/**
 * Loudrr Mini App — WaitlistPendingScreen
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function WaitlistPendingScreen({ xUsername, referralCode }: { xUsername?: string; referralCode?: string }) {
  const [copied, setCopied] = useState(false);

  const SITE_URL = typeof window !== 'undefined' ? window.location.origin : '';
  const BOT_USERNAME = 'loudrr_bot';
  const sharePageUrl = xUsername ? `${SITE_URL}/waitlist/${xUsername}` : SITE_URL;

  const [enrichment, setEnrichment] = useState<WaitlistEnrichment | null>(null);

  useEffect(() => {
    if (!xUsername) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getWaitlistEnrichment();
        if (!cancelled) setEnrichment(data);
      } catch {
        // best-effort; leave card unenriched
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cardImageUrl = xUsername
    ? (() => {
        const p = new URLSearchParams({ username: xUsername });
        // Only overlay enrichment when the backend resolved the SAME handle
        // we're rendering. Otherwise (opened with ?u=someoneelse) we'd paint
        // the caller's score onto another user's card.
        const showEnriched =
          enrichment &&
          enrichment.x_username &&
          enrichment.x_username.toLowerCase() === xUsername.toLowerCase();
        if (showEnriched && enrichment) {
          if (typeof enrichment.score === 'number') {
            p.set('score', String(Math.round(enrichment.score)));
          }
          if (enrichment.tier) {
            p.set('tier', enrichment.tier);
          }
          if (enrichment.followers.length > 0) {
            p.set('followers', enrichment.followers.join(','));
            p.set('followersCount', String(enrichment.followers_count));
          }
        }
        return `/api/cards/waitlist?${p.toString()}`;
      })()
    : null;
  // Direct mini-app deep link — startapp lands in the WebView with
  // initDataUnsafe.start_param set, so the referral survives into the app
  // (a plain ?start= bot link would need a /start handler to forward it).
  const referralLink = referralCode
    ? `https://t.me/${BOT_USERNAME}/app?startapp=ref_${referralCode}`
    : `https://t.me/${BOT_USERNAME}`;
  const shareText = `I just joined the @loudrrHQ waitlist!\n\nJoin me 👇\n${referralLink}`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareText);
      setCopied(true);
      hapticFeedback('success');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      hapticFeedback('error');
    }
  };

  const handleShareX = () => {
    hapticFeedback('light');
    const tweetText = `I just joined the @loudrrHQ waitlist!`;
    const url = `https://x.com/intent/tweet?text=${encodeURIComponent(tweetText)}&url=${encodeURIComponent(sharePageUrl)}`;
    openLink(url);
  };

  const handleShareTG = () => {
    hapticFeedback('light');
    const url = `https://t.me/share/url?url=${encodeURIComponent(referralLink)}&text=${encodeURIComponent('I just joined the @loudrrHQ waitlist! Join me 👇')}`;
    openLink(url);
  };

  return (
    <div className="min-h-screen bg-black flex flex-col items-center justify-center p-6">
      {/* Logo */}
      <div className="mb-6">
        <img src="/loudrr-icon.png" alt="Loudrr" className="w-20 h-20" />
      </div>

      {/* Message */}
      <h1 className="text-2xl font-bold text-white mb-2">You're on the Waitlist</h1>
      <p className="text-gray-400 text-center mb-4 max-w-sm">
        We'll notify you on Telegram when your account is approved.
      </p>

      {/* Card Image */}
      {cardImageUrl && (
        <img
          src={cardImageUrl}
          alt="Your Loudrr waitlist card"
          className="w-full max-w-sm mb-6"
          style={{ display: 'block' }}
        />
      )}

      {/* Share Buttons */}
      <div className="flex gap-3 mb-6">
        {/* Copy Button */}
        <button
          onClick={handleCopy}
          className="flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-medium transition-all"
          style={{
            background: 'rgba(255, 255, 255, 0.06)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            color: copied ? '#22c55e' : '#fff',
          }}
        >
          {copied ? (
            <CheckIconFill className="w-4 h-4" style={{ color: '#22c55e' }} />
          ) : (
            <ClipboardIcon className="w-4 h-4" />
          )}
          {copied ? 'Copied!' : 'Copy'}
        </button>

        {/* X Share Button */}
        <button
          onClick={handleShareX}
          className="flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-medium transition-all"
          style={{
            background: 'rgba(255, 255, 255, 0.06)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            color: '#fff',
          }}
        >
          <XLogoIcon className="w-4 h-4" />
          Post
        </button>

        {/* TG Share Button */}
        <button
          onClick={handleShareTG}
          className="flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-medium transition-all"
          style={{
            background: 'rgba(255, 255, 255, 0.06)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            color: '#fff',
          }}
        >
          <TelegramIcon className="w-4 h-4" />
          Share
        </button>
      </div>

      {/* Info */}
      <p className="text-gray-500 text-sm text-center max-w-xs">
        Thank you for your patience. High-quality accounts are prioritized.
      </p>
    </div>
  );
}
