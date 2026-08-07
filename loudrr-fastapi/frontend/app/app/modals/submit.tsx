'use client';

import React, { useState, useEffect } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { api, Post, User, SubmitPostResponse, AppSettings } from '@/lib/api';
import { formatKarma } from '../shared';
import { XIconFill } from '../icons';

/**
 * Loudrr Mini App — SubmitModal
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function SubmitModal({
  isOpen,
  onClose,
  user,
  onUserUpdate,
  settings,
}: {
  isOpen: boolean;
  onClose: () => void;
  user: User | null;
  onUserUpdate: () => void;
  settings: AppSettings | null;
}) {
  const [xLink, setXLink] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SubmitPostResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Karma amount defaults to minimum (settings?.post_cost_min or fallback 20)
  const minCost = settings?.post_cost_min ?? 20;
  const maxCost = settings?.post_cost_max ?? 40;
  const [karmaAmount, setKarmaAmount] = useState(minCost);

  // Reset karmaAmount when modal opens or settings change
  useEffect(() => {
    if (isOpen) {
      setKarmaAmount(minCost);
    }
  }, [isOpen, minCost]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!xLink.trim() || submitting) return;

    setSubmitting(true);
    setError(null);
    setResult(null);

    try {
      hapticFeedback('medium');
      const response = await api.submitPost(xLink.trim(), karmaAmount);
      setResult(response);
      setXLink('');
      onUserUpdate();
      hapticFeedback('success');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit post');
      hapticFeedback('error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = () => {
    if (!submitting) {
      setXLink('');
      setResult(null);
      setError(null);
      setKarmaAmount(minCost);
      onClose();
    }
  };

  const canSubmit = user && user.credits >= karmaAmount;

  if (!isOpen) return null;

  // Glass card style matching Stats modal
  const glassCardStyle = {
    background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
    backdropFilter: 'blur(32px) saturate(160%)',
    WebkitBackdropFilter: 'blur(32px) saturate(160%)',
    border: '1px solid rgba(249, 84, 0, 0.15)',
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset'
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/80 backdrop-blur-md"
        onClick={handleClose}
      />

      {/* Modal */}
      <div
        className="relative w-full max-w-lg rounded-t-3xl max-h-[85vh] flex flex-col animate-slide-up"
        style={{
          background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.95) 50%, rgba(249, 84, 0, 0.02) 100%)',
          backdropFilter: 'blur(32px) saturate(160%)',
          WebkitBackdropFilter: 'blur(32px) saturate(160%)',
          border: '1px solid rgba(249, 84, 0, 0.2)',
          borderBottom: 'none',
          boxShadow: '0 -4px 40px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.1) inset'
        }}
      >
        {/* Handle */}
        <div className="flex justify-center pt-3 pb-2">
          <div className="w-10 h-1 rounded-full bg-[#f95400]/40" />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-4 pb-4 border-b border-[#f95400]/15">
          <div>
            <h2 className="text-xl font-bold">Submit Post</h2>
            <p className="text-gray-400 text-sm">Share your X post to get engagements</p>
          </div>
          <button
            onClick={handleClose}
            className="w-8 h-8 rounded-full flex items-center justify-center transition-colors hover:bg-white/10"
            style={{ background: 'rgba(0, 0, 0, 0.3)' }}
          >
            <XIconFill className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Karma Selection */}
          <div className="p-4 space-y-4 rounded-2xl" style={glassCardStyle}>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-400">Karma to Spend</p>
                <p className="text-2xl font-bold gold-gradient-text">{formatKarma(karmaAmount)}</p>
              </div>
              <div className="text-right">
                <p className="text-sm text-gray-400">Your Balance</p>
                <p className={`text-xl font-bold ${canSubmit ? 'gold-gradient-text' : 'text-gray-500'}`}>
                  {formatKarma(user?.credits || 0)}
                </p>
              </div>
            </div>

            {/* Karma Slider - dynamic based on minCost/maxCost from settings */}
            <div className="space-y-2">
              <input
                type="range"
                min={minCost}
                max={maxCost}
                step={5}
                value={karmaAmount}
                onChange={(e) => {
                  hapticFeedback('light');
                  setKarmaAmount(Number(e.target.value));
                }}
                className="w-full h-2 bg-black/50 rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-5 [&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[#f95400] [&::-webkit-slider-thumb]:shadow-lg [&::-webkit-slider-thumb]:shadow-[#f95400]/30 [&::-webkit-slider-thumb]:cursor-grab [&::-webkit-slider-thumb]:active:cursor-grabbing"
                disabled={submitting}
              />
              {/* Number labels */}
              <div className="flex justify-between px-[2px]">
                {Array.from({ length: Math.floor((maxCost - minCost) / 5) + 1 }, (_, i) => minCost + i * 5).map((amount) => (
                  <button
                    key={amount}
                    type="button"
                    onClick={() => {
                      hapticFeedback('light');
                      setKarmaAmount(amount);
                    }}
                    disabled={submitting}
                    className={`text-xs transition-all ${karmaAmount === amount ? 'text-[#f95400] font-semibold' : 'text-gray-500'}`}
                  >
                    {amount}
                  </button>
                ))}
              </div>
            </div>

            <p className="text-xs text-gray-500 text-center">
              Higher karma = more engagements · Rewards based on engager tiers
            </p>
          </div>

          {/* Submit Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-gray-400 mb-2">X Post Link</label>
              <input
                type="url"
                value={xLink}
                onChange={(e) => setXLink(e.target.value)}
                placeholder="https://x.com/username/status/..."
                className="w-full px-4 py-3 rounded-xl text-white placeholder-gray-500 transition-all focus:outline-none focus:ring-2 focus:ring-[#f95400]/50"
                style={{
                  background: 'rgba(0, 0, 0, 0.4)',
                  border: '1px solid rgba(249, 84, 0, 0.2)',
                }}
                disabled={submitting}
              />
            </div>

            {error && (
              <div className="rounded-xl p-4" style={{ ...glassCardStyle, border: '1px solid rgba(249, 84, 0, 0.3)' }}>
                <p className="text-[#f95400] text-sm">{error}</p>
              </div>
            )}

            {result?.success && (
              <div className="rounded-xl p-4" style={{ ...glassCardStyle, border: '1px solid rgba(249, 84, 0, 0.4)' }}>
                <p className="text-[#f95400] text-sm">{result.message}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={!canSubmit || !xLink.trim() || submitting}
              className="w-full h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95 disabled:opacity-50"
              style={{
                background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
                backdropFilter: 'blur(16px)',
                WebkitBackdropFilter: 'blur(16px)',
                border: '1px solid rgba(249, 84, 0, 0.4)',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
                color: 'white',
              }}
            >
              {submitting ? 'Submitting...' : 'Submit Post'}
            </button>
          </form>

          {!canSubmit && user && (
            <div className="p-4 rounded-2xl" style={{ ...glassCardStyle, border: '1px solid rgba(249, 84, 0, 0.25)' }}>
              <p className="text-sm text-gray-300">
                You need <span className="gold-gradient-text font-semibold">{formatKarma(karmaAmount - user.credits)} more karma</span> to submit a post.
                Engage with posts to earn karma!
              </p>
            </div>
          )}

          {/* How it works */}
          <div className="space-y-3 pb-2">
            <h3 className="text-sm font-semibold text-gray-400">How it works</h3>
            <div className="space-y-2">
              {[
                `Select how much karma to spend (${minCost}-${maxCost})`,
                'Other users engage with your post',
                'They earn karma based on their tier until yours is depleted',
              ].map((text, i) => (
                <div key={i} className="flex items-start gap-3">
                  <div
                    className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0"
                    style={{ background: 'rgba(249, 84, 0, 0.15)', border: '1px solid rgba(249, 84, 0, 0.3)' }}
                  >
                    <span className="text-xs gold-gradient-text">{i + 1}</span>
                  </div>
                  <p className="text-sm text-gray-400">{text}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Tier note */}
          <p className="text-xs text-gray-500 text-center pb-4">
            Your karma is spent based on engager tiers. Same tier users are prioritized to balance your engagements.
          </p>
        </div>
      </div>
    </div>
  );
}
