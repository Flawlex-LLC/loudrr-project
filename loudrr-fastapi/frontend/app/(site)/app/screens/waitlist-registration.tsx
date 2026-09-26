'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { hapticFeedback, openXLogin } from '@/lib/telegram';
import { XLoginHelp } from '../components/x-login-help';
import { api, OtherPlatformEntry } from '@/lib/api';
import { DESIGN_MODE } from '@/lib/mockData';
import { ICON_GRADIENT_STYLE, REGIONS, NICHES } from '../shared';
import { BoltIconFill, XLogoIcon } from '../icons';

/**
 * Loudrr Mini App — WaitlistRegistrationScreen
 *
 * Flow (OAuth-first):
 *   Step 1: "Connect X" — backend OAuth start, X opens in the SYSTEM browser
 *           (Telegram openLink). That browser can't talk to this WebView, so
 *           we poll GET /waitlist/x-oauth/proof/ until the backend reports the
 *           outcome: a signed proof + the verified handle, or an error code.
 *   Step 2: Region.
 *   Step 3: Niche + other platforms + submit (with the proof in the body).
 *
 * The X handle is never typed by the user and never decoded from the token
 * client-side (itsdangerous compresses it — the old decoder returned null and
 * silently blocked Join for everyone). It comes from the poll response and
 * the register response. The server re-verifies the proof on register.
 */

const PROOF_KEY = 'x_oauth_proof';
const PROOF_USER_KEY = 'x_oauth_username';
const PROOF_EXP_KEY = 'x_oauth_proof_exp'; // epoch ms when the proof stops being valid
const REF_KEY = 'loudrr_ref';
// stop "Waiting for X…" if nothing comes back — X logins rarely take this long
const POLL_TIMEOUT_MS = 5 * 60 * 1000;
const POLL_EVERY_MS = 2500;
const POLL_BACKOFF_MS = 10_000;
// leave a margin so a proof isn't used seconds before the server rejects it
const PROOF_SAFETY_MS = 30_000;

const OAUTH_ERROR_COPY: Record<string, string> = {
  denied: 'You cancelled the X authorization. Try again to continue.',
  invalid: 'X returned an invalid response. Please try again.',
  expired: 'Your X session timed out. Please connect X again.',
  token: "Couldn't complete the handshake with X. Please try again.",
  profile: "Couldn't read your X profile. Please try again.",
  cancelled: 'That connection was cancelled in the browser. Tap Connect X to try again.',
};

/** Referral codes are 8 chars of [A-Z0-9_-]; anything else is dropped so a
 * mangled share link can't break sign-up. */
function cleanReferral(raw: string | null | undefined): string | null {
  const code = (raw || '').trim().toUpperCase();
  return /^[A-Z0-9_-]{4,16}$/.test(code) ? code : null;
}

function readStored(key: string): string | null {
  try {
    return sessionStorage.getItem(key) ?? localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function WaitlistRegistrationScreen({
  onSuccess,
}: {
  onSuccess: (data: { x_username: string; referral_code?: string }) => void;
}) {
  const [step, setStep] = useState(1);
  const [xProof, setXProof] = useState<string | null>(null);
  const [xUsername, setXUsername] = useState<string | null>(null);
  const [region, setRegion] = useState('');
  const [niche, setNiche] = useState('');
  const [otherPlatforms, setOtherPlatforms] = useState<Set<string>>(new Set());
  const [youtubeUsername, setYoutubeUsername] = useState('');
  const [tiktokUsername, setTiktokUsername] = useState('');
  const [otherPlatformName, setOtherPlatformName] = useState('');
  const [otherPlatformUsername, setOtherPlatformUsername] = useState('');
  const [loading, setLoading] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [waitingForOAuth, setWaitingForOAuth] = useState(false);
  // the X sign-in link, for the copy-into-Chrome/Safari fallback
  const [xAuthUrl, setXAuthUrl] = useState<string | null>(null);
  // someone authorized on X but hasn't confirmed the link in the browser yet
  // (that confirm step is what stops a forwarded authorize link binding
  // someone else's X account to this Telegram account)
  const [awaitingConfirm, setAwaitingConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Referral: ?ref= on the URL, else what the Telegram startapp capture
  // stored (session first, then local — survives a WebView restart).
  const [referralCode, setReferralCode] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const fromUrl = new URLSearchParams(window.location.search).get('ref');
    setReferralCode(cleanReferral(fromUrl) ?? cleanReferral(readStored(REF_KEY)));
  }, []);

  const forgetProof = useCallback(() => {
    try {
      for (const key of [PROOF_KEY, PROOF_USER_KEY, PROOF_EXP_KEY]) sessionStorage.removeItem(key);
    } catch { /* storage unavailable */ }
  }, []);

  const applyProof = useCallback((proof: string, username: string | null, expiresInSec: number | null) => {
    const expiresAt = Date.now() + Math.max(0, (expiresInSec ?? 600) * 1000 - PROOF_SAFETY_MS);
    try {
      sessionStorage.setItem(PROOF_KEY, proof);
      sessionStorage.setItem(PROOF_USER_KEY, username || '');
      sessionStorage.setItem(PROOF_EXP_KEY, String(expiresAt));
    } catch { /* storage unavailable — state below still carries it */ }
    setXProof(proof);
    setXUsername(username);
    setStep((s) => (s < 2 ? 2 : s));
    setWaitingForOAuth(false);
    setError(null);
  }, []);

  // Full reset back to step 1 (expired proof, X account taken, user's choice).
  const resetToConnectX = useCallback((message: string | null) => {
    forgetProof();
    setXProof(null);
    setXUsername(null);
    setWaitingForOAuth(false);
    setAwaitingConfirm(false);
    setStep(1);
    setError(message);
    if (message) hapticFeedback('error');
  }, [forgetProof]);

  // Mount: restore a still-valid proof from this WebView session, then ask
  // the server — it keeps the proof until registration, so a restart of
  // Telegram mid-flow doesn't cost another trip to X.
  useEffect(() => {
    if (DESIGN_MODE) return;
    let cancelled = false;
    const exp = Number(readStored(PROOF_EXP_KEY));
    const stored = readStored(PROOF_KEY);
    if (stored && Number.isFinite(exp) && exp > Date.now()) {
      applyProof(stored, readStored(PROOF_USER_KEY) || null, Math.round((exp - Date.now()) / 1000) + PROOF_SAFETY_MS / 1000);
    } else if (stored) {
      forgetProof();
    }
    (async () => {
      try {
        const r = await api.pollWaitlistXOAuthProof();
        if (cancelled) return;
        if (r.proof) applyProof(r.proof, r.x_username, r.expires_in);
      } catch { /* not signed in / offline — the Connect X button still works */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // While waiting for OAuth: poll the backend for the outcome. Errors end the
  // wait with a message; a failing poll (429 / network) backs off; and the
  // wait gives up after POLL_TIMEOUT_MS instead of spinning forever.
  const pollInFlight = useRef(false);
  useEffect(() => {
    if (!waitingForOAuth || xProof || DESIGN_MODE) return;
    const startedAt = Date.now();
    let backoffUntil = 0;
    let stopped = false;

    const tick = async () => {
      if (stopped || pollInFlight.current || Date.now() < backoffUntil) return;
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        stopped = true;
        setWaitingForOAuth(false);
        setError("We didn't hear back from X. Tap Connect X to try again.");
        hapticFeedback('error');
        return;
      }
      pollInFlight.current = true;
      try {
        const r = await api.pollWaitlistXOAuthProof();
        if (stopped) return;
        if (r.proof) {
          stopped = true;
          setAwaitingConfirm(false);
          applyProof(r.proof, r.x_username, r.expires_in);
          hapticFeedback('success');
        } else if (r.error) {
          stopped = true;
          setAwaitingConfirm(false);
          setWaitingForOAuth(false);
          setError(OAUTH_ERROR_COPY[r.error] || 'X authorization failed. Please try again.');
          hapticFeedback('error');
        } else if (r.awaiting_confirmation) {
          setAwaitingConfirm(true);
        }
      } catch {
        backoffUntil = Date.now() + POLL_BACKOFF_MS;
      } finally {
        pollInFlight.current = false;
      }
    };

    const interval = window.setInterval(() => { void tick(); }, POLL_EVERY_MS);
    // Telegram fires visibilitychange when the user swipes back from the browser
    const onVisible = () => {
      if (document.visibilityState === 'visible') void tick();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [waitingForOAuth, xProof, applyProof]);

  const togglePlatform = (platform: string) => {
    setOtherPlatforms(prev => {
      const next = new Set(prev);
      if (next.has(platform)) {
        next.delete(platform);
      } else {
        next.add(platform);
      }
      return next;
    });
  };

  const handleConnectX = async () => {
    if (connecting) return;
    setError(null);
    setConnecting(true);

    // Design-mode short-circuit: fake a proof so the wizard progresses
    // without a real X handshake.
    if (DESIGN_MODE) {
      try {
        applyProof('mock.design.proof', 'alexrivera', 600);
        hapticFeedback('success');
      } finally {
        setConnecting(false);
      }
      return;
    }

    try {
      const { authorize_url } = await api.startWaitlistXOAuth();
      hapticFeedback('light');
      setWaitingForOAuth(true);
      setXAuthUrl(authorize_url);
      openXLogin(authorize_url);
    } catch (e: any) {
      setError(e?.message || 'Failed to start X verification');
      hapticFeedback('error');
      setWaitingForOAuth(false);
      setAwaitingConfirm(false);
    } finally {
      setConnecting(false);
    }
  };

  const handleSubmit = async () => {
    if (!xProof || !region || !niche || loading) return;

    // the proof may have aged out while the user lingered on steps 2/3
    if (!DESIGN_MODE) {
      const exp = Number(readStored(PROOF_EXP_KEY));
      if (Number.isFinite(exp) && exp > 0 && exp <= Date.now()) {
        resetToConnectX('Your X session expired. Please connect X again.');
        return;
      }
    }

    setLoading(true);
    setError(null);

    try {
      const platforms: OtherPlatformEntry[] = [];
      if (otherPlatforms.has('youtube') && youtubeUsername.trim())
        platforms.push({ platform: 'youtube', username: youtubeUsername.trim() });
      if (otherPlatforms.has('tiktok') && tiktokUsername.trim())
        platforms.push({ platform: 'tiktok', username: tiktokUsername.trim() });
      if (otherPlatforms.has('other') && otherPlatformUsername.trim())
        platforms.push({ platform: 'other', username: otherPlatformUsername.trim(), platform_name: otherPlatformName.trim() || undefined });

      const result = await api.registerWaitlist({
        x_proof: xProof,
        referral_code: referralCode || undefined,
        region,
        niche,
        other_platforms: platforms.length ? platforms : undefined,
      });
      if (result.status === 'registered' || result.status === 'already_registered') {
        hapticFeedback('success');
        forgetProof();
        onSuccess({ x_username: result.x_username || xUsername || '', referral_code: result.referral_code });
      }
    } catch (err: any) {
      const msg: string = err?.message || 'Registration failed';
      if (/OAuth proof|different Telegram user/i.test(msg)) {
        // expired / invalid / someone else's proof — reconnect
        resetToConnectX('Your X session expired. Please connect X again.');
      } else if (/X username already (registered|in use)|X account already registered/i.test(msg)) {
        resetToConnectX('That X account is already linked to another Telegram account. Connect a different X account.');
      } else if (/^429/.test(msg)) {
        setError('Too many attempts. Please wait a few minutes and try again.');
        hapticFeedback('error');
      } else {
        setError(msg.replace(/^\d{3}: /, ''));
        hapticFeedback('error');
      }
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    background: 'rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(249, 84, 0, 0.2)',
  };

  const nextStep = () => {
    setError(null);
    hapticFeedback('light');
    setStep(s => s + 1);
  };

  const prevStep = () => {
    setError(null);
    hapticFeedback('light');
    setStep(s => s - 1);
  };

  const stepTitles = ['Connect Your X', 'Your Region', 'Your Niche'];
  const stepSubtitles = [
    'Verify your X account to get started.',
    'Where are you based?',
    'What best describes your focus?',
  ];

  return (
    <div className="min-h-screen bg-black flex flex-col items-center p-6 overflow-y-auto">
      {/* Logo */}
      <div className="mb-4 mt-6">
        <img src="/loudrr-icon.png" alt="Loudrr" className="w-16 h-16" />
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-5">
        {[1, 2, 3].map(s => (
          <div
            key={s}
            className="h-1 rounded-full transition-all duration-300"
            style={{
              width: s === step ? '32px' : '12px',
              background: s <= step ? '#f95400' : 'rgba(255,255,255,0.15)',
            }}
          />
        ))}
      </div>

      {/* Title */}
      <h1 className="text-2xl font-bold text-white mb-1">{stepTitles[step - 1]}</h1>
      <p className="text-gray-400 text-center mb-6 max-w-sm text-sm">
        {stepSubtitles[step - 1]}
      </p>

      {/* Verified-handle chip on steps 2/3 */}
      {step > 1 && xUsername && (
        <div
          className="flex items-center gap-2 px-3 py-1.5 rounded-full mb-5 text-xs"
          style={{
            background: 'rgba(34, 197, 94, 0.08)',
            border: '1px solid rgba(34, 197, 94, 0.25)',
            color: 'rgba(255,255,255,0.85)',
          }}
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          <span>Connected as <span className="font-semibold">@{xUsername}</span></span>
        </div>
      )}
      {step > 1 && !loading && (
        <button
          type="button"
          onClick={() => resetToConnectX(null)}
          className="text-xs text-gray-500 underline underline-offset-2 -mt-3 mb-4"
        >
          Use a different X account
        </button>
      )}

      {/* Form Card */}
      <div
        className="w-full max-w-md rounded-2xl p-5 mb-8"
        style={{
          background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
          backdropFilter: 'blur(32px)',
          border: '1px solid rgba(249, 84, 0, 0.15)',
        }}
      >
        {/* ---- STEP 1: Connect X ---- */}
        {step === 1 && (
          <>
            <div className="mb-5">
              <div className="flex items-center gap-3 mb-4">
                <div className="glass-icon glass-icon-md glass-icon-orange pointer-events-none">
                  <XLogoIcon className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                </div>
                <div>
                  <div className="text-white font-medium text-sm">Verify with X</div>
                  <div className="text-gray-500 text-xs">
                    {awaitingConfirm
                      ? 'Almost there — confirm the connection in the tab that opened.'
                      : waitingForOAuth
                        ? 'Waiting for X… complete authorization in the tab that opened.'
                        : "We'll open X so you can approve Loudrr."}
                  </div>
                </div>
              </div>

              {error && (
                <p className="text-red-400 text-xs mb-3 text-center">{error}</p>
              )}

              <button
                onClick={handleConnectX}
                disabled={connecting}
                className="w-full h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95 disabled:opacity-50"
                style={{
                  background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
                  backdropFilter: 'blur(16px)',
                  border: '1px solid rgba(249, 84, 0, 0.4)',
                  boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
                  color: 'white',
                }}
              >
                {connecting ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Opening X…
                  </>
                ) : awaitingConfirm ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Confirm in your browser…
                  </>
                ) : waitingForOAuth ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Waiting for X…
                  </>
                ) : (
                  <>
                    <XLogoIcon className="w-4 h-4" />
                    Connect X
                  </>
                )}
              </button>

              {waitingForOAuth && (
                <p className="text-gray-600 text-xs text-center mt-3">
                  After authorizing on X, return here. We'll detect it automatically.
                </p>
              )}
              {waitingForOAuth && !awaitingConfirm && xAuthUrl && <XLoginHelp url={xAuthUrl} />}
            </div>
          </>
        )}

        {/* ---- STEP 2: Region ---- */}
        {step === 2 && (
          <>
            <div className="mb-5">
              <label className="text-sm text-gray-400 mb-1.5 block">Region</label>
              <div className="flex items-center gap-3">
                <div className="glass-icon glass-icon-md glass-icon-orange pointer-events-none">
                  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="#ffffff" strokeWidth={2}>
                    <circle cx="12" cy="12" r="10" />
                    <path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
                  </svg>
                </div>
                <select
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                  className="flex-1 px-4 py-3 rounded-xl text-white focus:outline-none focus:ring-2 focus:ring-[#f95400]/50 text-sm appearance-none cursor-pointer"
                  style={{
                    ...inputStyle,
                    backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%239ca3af' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10l-5 5z'/%3E%3C/svg%3E")`,
                    backgroundRepeat: 'no-repeat',
                    backgroundPosition: 'right 12px center',
                    paddingRight: '36px',
                    color: region ? '#ffffff' : '#6b7280',
                  }}
                >
                  <option value="" disabled>Select your region</option>
                  {REGIONS.map(r => (
                    <option key={r.value} value={r.value} style={{ background: '#1a1a1a', color: '#ffffff' }}>{r.label}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Next Button (no Back — step 1 is now a one-way OAuth handshake) */}
            <button
              onClick={nextStep}
              disabled={!region}
              className="w-full h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95 disabled:opacity-50"
              style={{
                background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
                backdropFilter: 'blur(16px)',
                border: '1px solid rgba(249, 84, 0, 0.4)',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
                color: 'white',
              }}
            >
              Next
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </>
        )}

        {/* ---- STEP 3: Niche + Other Platforms ---- */}
        {step === 3 && (
          <>
            {/* Niche Selector */}
            <div className="mb-4">
              <label className="text-sm text-gray-400 mb-2 block">Your Niche</label>
              <div className="flex flex-wrap gap-2">
                {NICHES.map(n => (
                  <button
                    key={n.value}
                    type="button"
                    onClick={() => { setNiche(niche === n.value ? '' : n.value); hapticFeedback('light'); }}
                    className="px-4 py-2 rounded-full text-sm font-medium transition-all"
                    style={{
                      background: niche === n.value ? 'rgba(249, 84, 0, 0.25)' : 'rgba(255, 255, 255, 0.04)',
                      border: niche === n.value ? '1px solid rgba(249, 84, 0, 0.6)' : '1px solid rgba(255, 255, 255, 0.1)',
                      color: niche === n.value ? '#f95400' : 'rgba(255, 255, 255, 0.6)',
                    }}
                    disabled={loading}
                  >
                    {n.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Other Platforms */}
            <div className="mb-5">
              <label className="text-sm text-gray-400 mb-2 block">Active on other platforms?</label>
              <div className="flex gap-2 mb-2">
                {[
                  { key: 'youtube', label: 'YouTube', color: '#ff0000' },
                  { key: 'tiktok', label: 'TikTok', color: '#00f2ea' },
                  { key: 'other', label: 'Other', color: '#a78bfa' },
                ].map(p => (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => { togglePlatform(p.key); hapticFeedback('light'); }}
                    className="flex-1 py-2 rounded-full text-xs font-medium transition-all"
                    style={{
                      background: otherPlatforms.has(p.key) ? `${p.color}20` : 'rgba(255, 255, 255, 0.04)',
                      border: otherPlatforms.has(p.key) ? `1px solid ${p.color}60` : '1px solid rgba(255, 255, 255, 0.1)',
                      color: otherPlatforms.has(p.key) ? p.color : 'rgba(255, 255, 255, 0.5)',
                    }}
                    disabled={loading}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {/* YouTube username input */}
              <div
                style={{
                  maxHeight: otherPlatforms.has('youtube') ? '60px' : '0',
                  opacity: otherPlatforms.has('youtube') ? 1 : 0,
                  overflow: 'hidden',
                  transition: 'max-height 300ms ease, opacity 200ms ease',
                }}
              >
                <input
                  type="text"
                  value={youtubeUsername}
                  onChange={(e) => setYoutubeUsername(e.target.value)}
                  placeholder="YouTube channel or @handle"
                  maxLength={100}
                  className="w-full px-4 py-2.5 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-red-500/30 text-sm mt-1"
                  style={{ ...inputStyle, borderColor: 'rgba(255, 0, 0, 0.2)' }}
                  disabled={loading}
                />
              </div>

              {/* TikTok username input */}
              <div
                style={{
                  maxHeight: otherPlatforms.has('tiktok') ? '60px' : '0',
                  opacity: otherPlatforms.has('tiktok') ? 1 : 0,
                  overflow: 'hidden',
                  transition: 'max-height 300ms ease, opacity 200ms ease',
                }}
              >
                <input
                  type="text"
                  value={tiktokUsername}
                  onChange={(e) => setTiktokUsername(e.target.value)}
                  placeholder="TikTok @username"
                  maxLength={100}
                  className="w-full px-4 py-2.5 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-cyan-500/30 text-sm mt-1"
                  style={{ ...inputStyle, borderColor: 'rgba(0, 242, 234, 0.2)' }}
                  disabled={loading}
                />
              </div>

              {/* Other platform inputs */}
              <div
                style={{
                  maxHeight: otherPlatforms.has('other') ? '120px' : '0',
                  opacity: otherPlatforms.has('other') ? 1 : 0,
                  overflow: 'hidden',
                  transition: 'max-height 300ms ease, opacity 200ms ease',
                }}
              >
                <input
                  type="text"
                  value={otherPlatformName}
                  onChange={(e) => setOtherPlatformName(e.target.value)}
                  placeholder="Platform name"
                  maxLength={50}
                  className="w-full px-4 py-2.5 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-purple-500/30 text-sm mt-1"
                  style={{ ...inputStyle, borderColor: 'rgba(167, 139, 250, 0.2)' }}
                  disabled={loading}
                />
                <input
                  type="text"
                  value={otherPlatformUsername}
                  onChange={(e) => setOtherPlatformUsername(e.target.value)}
                  placeholder="Username"
                  maxLength={100}
                  className="w-full px-4 py-2.5 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-purple-500/30 text-sm mt-1.5"
                  style={{ ...inputStyle, borderColor: 'rgba(167, 139, 250, 0.2)' }}
                  disabled={loading}
                />
              </div>
            </div>

            {/* Error Message */}
            {error && (
              <p className="text-red-400 text-sm mb-4 text-center">{error}</p>
            )}

            {/* Nav Buttons */}
            <div className="flex gap-3">
              <button
                onClick={prevStep}
                disabled={loading}
                className="flex-1 h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95"
                style={{
                  background: 'rgba(255, 255, 255, 0.04)',
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                  color: 'rgba(255, 255, 255, 0.6)',
                }}
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                </svg>
                Back
              </button>
              <button
                onClick={handleSubmit}
                disabled={!niche || loading}
                className="flex-1 h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95 disabled:opacity-50"
                style={{
                  background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
                  backdropFilter: 'blur(16px)',
                  border: '1px solid rgba(249, 84, 0, 0.4)',
                  boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
                  color: 'white',
                }}
              >
                {loading ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Registering...
                  </>
                ) : (
                  <>
                    <BoltIconFill className="w-5 h-5" />
                    Join Waitlist
                  </>
                )}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
