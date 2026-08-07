'use client';

import { useState } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { User } from '@/lib/api';
import { ICON_GRADIENT_STYLE, formatKarma, getScoreMultiplier, getScoreTier } from '../shared';
import { BoltIconFill, WalletIconFill, TrophyIconFill, XIconFill, TrendingUpIconFill, TargetIconFill } from '../icons';
import { StreakCard } from '../components/leaf';

/**
 * Loudrr Mini App — HomeTab
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function HomeTab({ user, onRefresh }: { user: User | null; onRefresh: () => void }) {
  const [showTierInfo, setShowTierInfo] = useState(false);

  if (!user) {
    return (
      <div className="p-4 text-center">
        <p className="text-gray-400">Could not load user data</p>
        <button onClick={onRefresh} className="btn-primary mt-4">Retry</button>
      </div>
    );
  }

  const tweetscoutScore = user.tweetscout_score || 0;
  const scoreMultiplier = getScoreMultiplier(tweetscoutScore);
  const scoreTier = getScoreTier(tweetscoutScore);

  // Tier data for the info modal
  const tierData = [
    { name: 'GOAT', minPoints: 1000, multiplier: '1.35x' },
    { name: 'OG', minPoints: 800, multiplier: '1.30x' },
    { name: 'Legend', minPoints: 600, multiplier: '1.25x' },
    { name: 'Based', minPoints: 400, multiplier: '1.20x' },
    { name: 'Degen', minPoints: 200, multiplier: '1.15x' },
    { name: 'Normie', minPoints: 100, multiplier: '1.10x' },
    { name: 'Anon', minPoints: 0, multiplier: '1.0x' },
  ];

  return (
    <div className="p-4 space-y-4">
      {/* Balance Card - High-Tech Design */}
      <div className="relative overflow-hidden rounded-2xl border border-[#f95400]/20 bg-gradient-to-br from-black via-zinc-900/50 to-black">
        {/* Grid Pattern Background */}
        <div className="absolute inset-0 opacity-[0.07]" style={{
          backgroundImage: `linear-gradient(#f95400 1px, transparent 1px), linear-gradient(90deg, #f95400 1px, transparent 1px)`,
          backgroundSize: '24px 24px'
        }} />

        {/* Scan line effect */}
        <div className="absolute inset-0 bg-gradient-to-b from-transparent via-[#f95400]/[0.03] to-transparent" />

        <div className="relative z-10 p-6">
          {/* Header Row */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="glass-icon glass-icon-md glass-icon-orange">
                <WalletIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
              </div>
              <div>
                <p className="text-sm text-[#f95400] uppercase tracking-wider">Balance</p>
                <p className="text-xs text-white">{scoreMultiplier} multiplier</p>
              </div>
            </div>
            <button
              onClick={() => setShowTierInfo(true)}
              className="px-4 py-2 rounded-full flex items-center gap-2 transition-all active:scale-95 cursor-pointer"
              style={{
                background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
                backdropFilter: 'blur(16px)',
                WebkitBackdropFilter: 'blur(16px)',
                border: '1px solid rgba(249, 84, 0, 0.4)',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.4), 0 1px 0 rgba(255, 140, 66, 0.2) inset'
              }}
            >
              <span className="text-xs font-bold uppercase tracking-wide text-white">{scoreTier}</span>
            </button>
          </div>

          {/* Main Balance Display */}
          <div className="mb-6">
            <div className="flex items-baseline gap-2">
              <span className="text-5xl font-bold tracking-tight gold-gradient-text">{formatKarma(user.credits)}</span>
              <div className="flex items-center gap-1 mb-0.5">
                <BoltIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
                <span className="text-lg text-white font-light">karma</span>
              </div>
            </div>
          </div>

          {/* Engagement Progress - Magic UI card with BorderBeam */}
          <div className="relative rounded-2xl overflow-hidden p-4" style={{
            background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
            backdropFilter: 'blur(32px) saturate(160%)',
            WebkitBackdropFilter: 'blur(32px) saturate(160%)',
            border: '1px solid rgba(249, 84, 0, 0.15)',
            boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset'
          }}>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <div className="glass-icon glass-icon-sm glass-icon-orange">
                  <TrendingUpIconFill className="w-3.5 h-3.5" style={ICON_GRADIENT_STYLE} />
                </div>
                <span className="text-sm font-semibold text-white">Today's Progress</span>
              </div>
              <span className="text-sm font-mono font-bold text-white">
                {user.engaged_today || 0}
                <span className="text-gray-400">/{(user.engaged_today || 0) + (user.available_posts || 0)}</span>
              </span>
            </div>
            <div className="h-3 bg-white/10 rounded-full overflow-hidden ring-1 ring-white/20">
              <div
                className="h-full rounded-full transition-all duration-500 relative overflow-hidden"
                style={{
                  background: 'linear-gradient(90deg, #f95400 0%, #ff8c42 50%, #f95400 100%)',
                  backgroundSize: '200% 100%',
                  animation: 'progress-shine 3s ease-in-out infinite',
                  boxShadow: '0 0 12px rgba(249, 84, 0, 0.3), 0 1px 0 rgba(255, 140, 66, 0.4) inset',
                  width: `${((user.engaged_today || 0) + (user.available_posts || 0)) > 0
                    ? ((user.engaged_today || 0) / ((user.engaged_today || 0) + (user.available_posts || 0))) * 100
                    : 0}%`
                }}
              />
            </div>
            {(user.available_posts || 0) > 0 ? (
              <p className="text-xs text-gray-300 font-medium mt-2">{user.available_posts} posts waiting for you</p>
            ) : (
              <p className="text-xs text-[#f95400] mt-2">All caught up! Submit a post to earn more</p>
            )}
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-2 gap-3">
        <button
          onClick={() => {
            hapticFeedback('light');
            const engageTab = document.querySelector('[data-tab="engage"]') as HTMLButtonElement;
            if (engageTab) engageTab.click();
          }}
          className="group relative overflow-hidden rounded-xl p-4 text-left transition-all active:scale-[0.98]"
          style={{
            background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.12) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.08) 100%)',
            backdropFilter: 'blur(32px) saturate(160%)',
            WebkitBackdropFilter: 'blur(32px) saturate(160%)',
            border: '1px solid rgba(249, 84, 0, 0.4)',
            boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.15) inset'
          }}
        >
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-[#f95400]/10 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
          <div className="glass-icon glass-icon-md glass-icon-orange mb-2">
            <BoltIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
          </div>
          <p className="text-sm font-bold text-white">Start Engaging</p>
          <p className="text-xs text-gray-300">Earn karma now</p>
        </button>
        <button
          onClick={() => {
            hapticFeedback('light');
            const campaignsTab = document.querySelector('[data-tab="campaigns"]') as HTMLButtonElement;
            if (campaignsTab) campaignsTab.click();
          }}
          className="group relative overflow-hidden rounded-xl bg-white/[0.03] border border-white/[0.08] p-4 text-left transition-all hover:border-[#f95400]/30 active:scale-[0.98]"
        >
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-[#f95400]/5 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
          <div className="glass-icon glass-icon-md glass-icon-orange mb-2">
            <TargetIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
          </div>
          <p className="text-sm font-semibold text-white">Campaigns</p>
          <p className="text-xs text-gray-500">Coming soon</p>
        </button>
      </div>

      {/* Streak Card */}
      <StreakCard currentStreak={user.current_streak} />

      {/* Tier Info Modal */}
      {showTierInfo && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/80 backdrop-blur-sm"
            onClick={() => setShowTierInfo(false)}
          />

          {/* Modal */}
          <div
            className="relative w-full max-w-sm rounded-2xl p-5 animate-slide-up"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
              backdropFilter: 'blur(32px) saturate(160%)',
              WebkitBackdropFilter: 'blur(32px) saturate(160%)',
              border: '1px solid rgba(249, 84, 0, 0.15)',
              boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset',
            }}
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-3">
                <div
                  className="w-9 h-9 rounded-xl flex items-center justify-center"
                  style={{
                    background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(249, 84, 0, 0.08) 100%)',
                    border: '1px solid rgba(249, 84, 0, 0.3)',
                  }}
                >
                  <TrophyIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
                </div>
                <h3 className="text-base font-semibold text-white">Creator Tiers</h3>
              </div>
              <button
                onClick={() => setShowTierInfo(false)}
                className="w-8 h-8 rounded-xl flex items-center justify-center transition-colors hover:bg-white/10"
                style={{
                  background: 'rgba(255, 255, 255, 0.05)',
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                }}
              >
                <XIconFill className="w-4 h-4 text-gray-400" />
              </button>
            </div>

            {/* Tiers List */}
            <div className="space-y-2 mb-5">
              {tierData.map((tier) => {
                const isCurrentTier = tier.name === scoreTier;
                const isAchieved = tweetscoutScore >= tier.minPoints;

                return (
                  <div
                    key={tier.name}
                    className="flex items-center justify-between py-2.5 px-3 rounded-xl transition-all"
                    style={{
                      background: isCurrentTier
                        ? 'linear-gradient(135deg, rgba(249, 84, 0, 0.15) 0%, rgba(249, 84, 0, 0.05) 100%)'
                        : 'transparent',
                      border: isCurrentTier
                        ? '1px solid rgba(249, 84, 0, 0.4)'
                        : '1px solid rgba(255, 255, 255, 0.06)',
                      opacity: !isAchieved && !isCurrentTier ? 0.4 : 1,
                    }}
                  >
                    <span className={`text-sm font-medium ${isCurrentTier ? 'text-[#f95400]' : 'text-white'}`}>
                      {tier.name}
                    </span>
                    <div className="flex items-center gap-4">
                      <span className="text-xs text-gray-500">
                        {tier.minPoints}+ pts
                      </span>
                      <span
                        className="text-xs font-mono font-semibold px-2 py-0.5 rounded-md"
                        style={{
                          background: isCurrentTier ? 'rgba(249, 84, 0, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                          color: isCurrentTier ? '#f95400' : '#9ca3af',
                        }}
                      >
                        {tier.multiplier}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Note */}
            <p className="text-xs text-gray-500 text-center">
              Your TweetScout score is <span className="text-[#f95400]">{Math.round(tweetscoutScore)}</span> and determines your multiplier
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
