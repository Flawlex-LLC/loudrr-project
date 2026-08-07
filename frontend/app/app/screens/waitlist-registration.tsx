'use client';

import { useState, useEffect } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { api, OtherPlatformEntry } from '@/lib/api';
import { ICON_GRADIENT_STYLE, REGIONS, NICHES } from '../shared';
import { BoltIconFill, XLogoIcon } from '../icons';

/**
 * Loudrr Mini App — WaitlistRegistrationScreen
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function WaitlistRegistrationScreen({
  onSuccess,
}: {
  onSuccess: (data: { x_username: string; referral_code?: string }) => void;
}) {
  const [step, setStep] = useState(1);
  const [xLink, setXLink] = useState('');
  const [region, setRegion] = useState('');
  const [niche, setNiche] = useState('');
  const [otherPlatforms, setOtherPlatforms] = useState<Set<string>>(new Set());
  const [youtubeUsername, setYoutubeUsername] = useState('');
  const [tiktokUsername, setTiktokUsername] = useState('');
  const [otherPlatformName, setOtherPlatformName] = useState('');
  const [otherPlatformUsername, setOtherPlatformUsername] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Get referral code from URL query param (e.g., ?ref=ABC123)
  const [referralCode, setReferralCode] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const ref = params.get('ref');
      if (ref) {
        setReferralCode(ref);
        console.log('Referral code detected:', ref);
      }
    }
  }, []);

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

  const handleSubmit = async () => {
    if (!xLink || !region || !niche) return;

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

      const result = await api.registerWaitlist(
        xLink, referralCode || undefined,
        region, niche, platforms.length ? platforms : undefined
      );
      if (result.status === 'registered' || result.status === 'already_registered') {
        hapticFeedback('success');
        const username = xLink.replace(/^https?:\/\/(www\.)?(twitter\.com|x\.com)\//, '').replace(/\/.*$/, '').replace(/^@/, '');
        onSuccess({ x_username: username, referral_code: result.referral_code });
      }
    } catch (err: any) {
      setError(err.message || 'Registration failed');
      hapticFeedback('error');
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

  const stepTitles = ['Your X Profile', 'Your Region', 'Your Niche'];
  const stepSubtitles = [
    'Drop your X profile link to get started.',
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

      {/* Form Card */}
      <div
        className="w-full max-w-md rounded-2xl p-5 mb-8"
        style={{
          background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
          backdropFilter: 'blur(32px)',
          border: '1px solid rgba(249, 84, 0, 0.15)',
        }}
      >
        {/* ---- STEP 1: X Profile ---- */}
        {step === 1 && (
          <>
            {/* X Profile Input */}
            <div className="mb-5">
              <label className="text-sm text-gray-400 mb-1.5 block">X Profile</label>
              <div className="flex items-center gap-3">
                <div className="glass-icon glass-icon-md glass-icon-orange pointer-events-none">
                  <XLogoIcon className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                </div>
                <input
                  type="url"
                  value={xLink}
                  onChange={(e) => setXLink(e.target.value)}
                  placeholder="https://x.com/yourhandle"
                  className="flex-1 px-4 py-3 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#f95400]/50 text-sm"
                  style={inputStyle}
                />
              </div>
            </div>

            {/* Next Button */}
            <button
              onClick={nextStep}
              disabled={!xLink}
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

            {/* Nav Buttons */}
            <div className="flex gap-3">
              <button
                onClick={prevStep}
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
                onClick={nextStep}
                disabled={!region}
                className="flex-1 h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95 disabled:opacity-50"
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
            </div>
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
