'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { hapticFeedback, openLink } from '@/lib/telegram';
import { api, OtherPlatformEntry } from '@/lib/api';
import { DESIGN_MODE } from '@/lib/mockData';
import { ICON_GRADIENT_STYLE, REGIONS, NICHES } from '../shared';
import { BoltIconFill, XLogoIcon } from '../icons';

/**
 * Loudrr Mini App — WaitlistRegistrationScreen
 *
 * Flow (post-OAuth-first refactor):
 *   Step 1: "Connect X" — one button. Kicks off backend OAuth start, opens
 *           X in an external tab, then polls sessionStorage for the signed
 *           proof that /waitlist/oauth-return dropped there on return.
 *   Step 2: Region.
 *   Step 3: Niche + other platforms + submit (with the proof in the body).
 *
 * The X handle is never typed by the user — it comes from the OAuth /users/me
 * call server-side and is baked into the signed proof. Client-side we decode
 * the first segment of the itsdangerous token only for display; the server
 * re-verifies signature + freshness on register.
 */

const PROOF_KEY = 'x_oauth_proof';
const PROOF_IAT_KEY = 'x_oauth_proof_iat';
const PROOF_ERR_KEY = 'x_oauth_error';
// Client-side freshness bound. Server enforces 10-min max_age on the signed
// proof itself; we tighten to 8 min here so a user coming back to a
// backgrounded tab gets an obvious "Please connect X again" instead of
// silently advancing through the whole form and 400-ing at submit.
const PROOF_MAX_AGE_MS = 8 * 60 * 1000;

const OAUTH_ERROR_COPY: Record<string, string> = {
  denied: 'You cancelled the X authorization. Try again to continue.',
  invalid: 'X returned an invalid response. Please try again.',
  expired: 'Your session timed out. Please connect X again.',
  token: "Couldn't complete the handshake with X. Please try again.",
  profile: "Couldn't read your X profile. Please try again.",
};

/**
 * itsdangerous URLSafeTimedSerializer emits `<b64json>.<b64ts>.<b64sig>`.
 * The first dot-separated segment is url-safe-base64 of the JSON payload.
 * We ONLY use this for a display hint (the verified @handle shown on step 2);
 * the server re-verifies the signature and enforces max_age on submit.
 */
function decodeProofUsername(proof: string): string | null {
  try {
    const first = proof.split('.')[0];
    if (!first) return null;
    // url-safe base64: convert to standard b64 and pad
    let b64 = first.replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    const json = typeof atob === 'function' ? atob(b64) : '';
    if (!json) return null;
    const payload = JSON.parse(json);
    if (payload && typeof payload.x_username === 'string') return payload.x_username;
    return null;
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
  const [error, setError] = useState<string | null>(null);

  // Get referral code from URL query param (e.g., ?ref=ABC123), falling back
  // to the value stashed in sessionStorage by the Telegram start_param capture
  // (startapp deep links don't survive router.replace as query params).
  const [referralCode, setReferralCode] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      let ref = params.get('ref');
      if (!ref) {
        try {
          ref = sessionStorage.getItem('loudrr_ref');
        } catch { /* sessionStorage unavailable */ }
      }
      if (ref) {
        setReferralCode(ref);
        console.log('Referral code detected:', ref);
      }
    }
  }, []);

  // Pick up the proof (or an error) that /waitlist/oauth-return stashed for us.
  const consumeStoredProof = useCallback(() => {
    if (typeof window === 'undefined') return false;
    try {
      const err = sessionStorage.getItem(PROOF_ERR_KEY);
      if (err) {
        sessionStorage.removeItem(PROOF_ERR_KEY);
        setError(OAUTH_ERROR_COPY[err] || 'X authorization failed. Please try again.');
        setWaitingForOAuth(false);
        hapticFeedback('error');
      }
      const proof = sessionStorage.getItem(PROOF_KEY);
      if (!proof) return false;

      // Freshness check — stale proofs go to the wall, user is bounced back
      // to step 1 with an actionable message. Without this, the user would
      // march to step 3 and 400 at submit.
      const iatStr = sessionStorage.getItem(PROOF_IAT_KEY);
      const iat = iatStr ? Number(iatStr) : NaN;
      const ageOk = Number.isFinite(iat) && Date.now() - iat < PROOF_MAX_AGE_MS;
      if (!ageOk) {
        sessionStorage.removeItem(PROOF_KEY);
        sessionStorage.removeItem(PROOF_IAT_KEY);
        setXProof(null);
        setXUsername(null);
        setStep(1);
        setWaitingForOAuth(false);
        setError('Your X session expired. Please connect X again.');
        hapticFeedback('error');
        return false;
      }

      const username = decodeProofUsername(proof);
      setXProof(proof);
      setXUsername(username);
      setStep(s => (s < 2 ? 2 : s));
      setWaitingForOAuth(false);
      setError(null);
      hapticFeedback('success');
      return true;
    } catch {
      // sessionStorage unavailable — no-op.
    }
    return false;
  }, []);

  // Mount check — user may already have OAuth'd in a prior visit.
  useEffect(() => {
    consumeStoredProof();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Guard against overlapping server-side proof polls.
  const proofPollInFlight = useRef(false);

  // While waiting for OAuth to return, poll sessionStorage + listen for
  // window focus (Telegram in-app browser fires visibilitychange when the
  // user swipes back to the WebView).
  //
  // ALSO poll the backend for a server-stored proof: inside Telegram,
  // openLink() completes the OAuth chain in the external SYSTEM browser,
  // whose sessionStorage the mini-app WebView can never see. The backend
  // stores the minted proof keyed by telegram_id; we fetch and consume it
  // here, then feed it through the same sessionStorage path.
  useEffect(() => {
    if (!waitingForOAuth || xProof) return;
    const pollServerProof = async () => {
      if (DESIGN_MODE || proofPollInFlight.current) return;
      proofPollInFlight.current = true;
      try {
        const { proof } = await api.pollWaitlistXOAuthProof();
        if (proof) {
          try {
            sessionStorage.setItem(PROOF_KEY, proof);
            sessionStorage.setItem(PROOF_IAT_KEY, String(Date.now()));
          } catch { /* sessionStorage unavailable */ }
          // The server row is consumed (one-shot DELETE...RETURNING) — never
          // depend on the sessionStorage round-trip alone. If the stored copy
          // isn't readable back, apply the proof to state directly so it
          // can't be lost.
          if (!consumeStoredProof()) {
            setXProof(proof);
            setXUsername(decodeProofUsername(proof));
            setStep(s => (s < 2 ? 2 : s));
            setWaitingForOAuth(false);
            setError(null);
            hapticFeedback('success');
          }
        }
      } catch {
        // best-effort — keep polling
      } finally {
        proofPollInFlight.current = false;
      }
    };
    // 2.5s = 24 requests/minute, safely under the endpoint's 30/minute limit
    // (a 1.5s tick would trip 429s after ~45s of waiting).
    const interval = window.setInterval(() => {
      consumeStoredProof();
      void pollServerProof();
    }, 2500);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') consumeStoredProof();
    };
    const onFocus = () => consumeStoredProof();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('focus', onFocus);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('focus', onFocus);
    };
  }, [waitingForOAuth, xProof, consumeStoredProof]);

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

    // Design-mode short-circuit: fabricate a proof so the wizard progresses
    // without hitting a real X handshake.
    if (DESIGN_MODE) {
      try {
        const fakeUsername = 'alexrivera';
        const fakePayload = btoa(JSON.stringify({
          tg_id: 0,
          x_username: fakeUsername,
          x_user_id: 'mock-x-id',
          iat: Math.floor(Date.now() / 1000),
        })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
        const fakeProof = `${fakePayload}.mocktimestamp.mocksignature`;
        try {
          sessionStorage.setItem(PROOF_KEY, fakeProof);
          sessionStorage.setItem(PROOF_IAT_KEY, String(Date.now()));
        } catch { /* ignore */ }
        setXProof(fakeProof);
        setXUsername(fakeUsername);
        setStep(2);
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
      openLink(authorize_url);
    } catch (e: any) {
      setError(e?.message || 'Failed to start X verification');
      hapticFeedback('error');
      setWaitingForOAuth(false);
    } finally {
      setConnecting(false);
    }
  };

  // Full reset back to step 1 when the proof is expired/invalid — clears the
  // stored proof and asks the user to re-connect X.
  const resetToConnectX = useCallback(() => {
    try {
      sessionStorage.removeItem(PROOF_KEY);
      sessionStorage.removeItem(PROOF_IAT_KEY);
    } catch { /* ignore */ }
    setXProof(null);
    setXUsername(null);
    setWaitingForOAuth(false);
    setStep(1);
    setError('Your X session expired. Please connect X again.');
    hapticFeedback('error');
  }, []);

  const handleSubmit = async () => {
    if (!xProof || !xUsername || !region || !niche) return;

    // Belt-and-braces freshness re-check before hitting the network — the
    // proof may have aged out while the user lingered on steps 2/3.
    if (!DESIGN_MODE) {
      let iat = NaN;
      try {
        const iatStr = sessionStorage.getItem(PROOF_IAT_KEY);
        iat = iatStr ? Number(iatStr) : NaN;
      } catch { /* sessionStorage unavailable — let the server decide */ }
      if (Number.isFinite(iat) && Date.now() - iat >= PROOF_MAX_AGE_MS) {
        resetToConnectX();
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
        platforms.push({ platform: 'other', username: otherPlatformUsername.trim(), platform_name: otherPlatformName.trim() });

      const result = await api.registerWaitlist({
        x_proof: xProof,
        referral_code: referralCode || undefined,
        region,
        niche,
        other_platforms: platforms.length ? platforms : undefined,
      });
      if (result.status === 'registered' || result.status === 'already_registered') {
        hapticFeedback('success');
        try {
          sessionStorage.removeItem(PROOF_KEY);
          sessionStorage.removeItem(PROOF_IAT_KEY);
        } catch { /* ignore */ }
        onSuccess({ x_username: xUsername, referral_code: result.referral_code });
      }
    } catch (err: any) {
      const msg: string = err?.message || 'Registration failed';
      // Server rejected the proof (expired/invalid/wrong Telegram user) —
      // bounce back to step 1 so the user can re-connect instead of being
      // stuck 400-ing on step 3.
      if (/OAuth proof|different Telegram user/i.test(msg)) {
        resetToConnectX();
      } else {
        setError(msg);
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
                    {waitingForOAuth
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
                  className="w-full px-4 py-2.5 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-purple-500/30 text-sm mt-1"
                  style={{ ...inputStyle, borderColor: 'rgba(167, 139, 250, 0.2)' }}
                  disabled={loading}
                />
                <input
                  type="text"
                  value={otherPlatformUsername}
                  onChange={(e) => setOtherPlatformUsername(e.target.value)}
                  placeholder="Username"
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
