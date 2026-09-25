'use client';

import { useState, useEffect, useRef } from 'react';
import { hapticFeedback, openLink } from '@/lib/telegram';
import { api, type WaitlistEnrichment } from '@/lib/api';
import { BOT_APP_URL } from '@/lib/bot';
import { ClipboardIcon, CheckIconFill, XLogoIcon, TelegramIcon } from '../icons';
import { describeScoreRefresh } from '../shared';

/**
 * Loudrr Mini App — WaitlistPendingScreen
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function WaitlistPendingScreen({ xUsername, referralCode }: { xUsername?: string; referralCode?: string }) {
  const [copied, setCopied] = useState(false);

  const SITE_URL = typeof window !== 'undefined' ? window.location.origin : '';
  const sharePageUrl = xUsername ? `${SITE_URL}/waitlist/${xUsername}` : SITE_URL;

  const [enrichment, setEnrichment] = useState<WaitlistEnrichment | null>(null);
  const [pollingDone, setPollingDone] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  // a Refresh tap supersedes the sign-up poll: a poll response landing after
  // the refresh must not overwrite the fresher card
  const pollCancelled = useRef(false);

  useEffect(() => {
    if (!xUsername) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The score is fetched in the background at sign-up (a slow lookup can
    // take ~30s, and the backend retries it if the provider is unavailable).
    // While it's still 'pending', re-read the stored card for about a minute
    // (this read never scrapes), then leave it to the Refresh button.
    const load = async (attempt: number) => {
      if (cancelled || pollCancelled.current) return;
      try {
        const data = await api.getWaitlistEnrichment();
        if (cancelled || pollCancelled.current) return;
        setEnrichment(data);
        if (data.score_status === 'pending' && attempt < 20) {
          timer = setTimeout(() => load(attempt + 1), 3000);
        } else {
          setPollingDone(true);
        }
      } catch {
        // best-effort; leave card unenriched
        if (!cancelled) setPollingDone(true);
      }
    };
    load(0);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRefreshScore = async () => {
    if (refreshing) return;
    hapticFeedback('light');
    pollCancelled.current = true;
    setPollingDone(true);
    setRefreshing(true);
    setRefreshNote(null);
    try {
      const result = await api.refreshScore();
      setEnrichment(result);
      setRefreshNote(describeScoreRefresh(result));
      hapticFeedback(result.result === 'updated' ? 'success' : 'warning');
    } catch (e) {
      setRefreshNote(e instanceof Error ? e.message : 'Could not refresh your score.');
      hapticFeedback('error');
    } finally {
      setRefreshing(false);
    }
  };

  const scoreNote =
    refreshNote ??
    (enrichment?.score_status === 'pending'
      ? pollingDone
        ? 'Your score is still loading. Tap Refresh score to try again.'
        : 'Calculating your score…'
      : enrichment?.score_status === 'not_found'
        ? "No score for your X account yet. Try again later."
        : null);

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
            // floor, like the backend's tier cut-off (399.6 is not "400")
            p.set('score', String(Math.floor(enrichment.score)));
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
    ? `${BOT_APP_URL}?startapp=ref_${referralCode}`
    : BOT_APP_URL;
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
          className="w-full max-w-sm mb-3"
          style={{ display: 'block' }}
        />
      )}

      {/* Score refresh — scores are fetched at sign-up and on this tap only */}
      {xUsername && (
        <div className="flex flex-col items-center gap-2 mb-6">
          <button
            onClick={handleRefreshScore}
            disabled={refreshing}
            className="px-4 py-2 rounded-xl text-sm font-medium transition-all disabled:opacity-60"
            style={{
              background: 'rgba(255, 255, 255, 0.06)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: '#fff',
            }}
          >
            {refreshing ? 'Refreshing…' : 'Refresh score'}
          </button>
          {scoreNote && (
            <p className="text-xs text-gray-400 text-center max-w-xs">{scoreNote}</p>
          )}
        </div>
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
