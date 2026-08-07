'use client';

import { useState, useEffect } from 'react';
import { api, UserStats } from '@/lib/api';
import { ICON_GRADIENT_STYLE, formatKarma } from '../shared';
import { BoltIconFill, ChartIconFill, XIconFill, TrendingUpIconFill, SendIconFill } from '../icons';

/**
 * Loudrr Mini App — StatsModal
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function StatsModal({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const [stats, setStats] = useState<UserStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      loadStats();
    }
  }, [isOpen]);

  const loadStats = async () => {
    try {
      setLoading(true);
      const data = await api.getUserStats();
      setStats(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load stats');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  const maxCredits = Math.max(stats?.user?.total_credits_earned || 0, stats?.user?.total_credits_spent || 0, 1);
  const earnedHeight = stats?.user ? (stats.user.total_credits_earned / maxCredits) * 100 : 0;
  const spentHeight = stats?.user ? (stats.user.total_credits_spent / maxCredits) * 100 : 0;

  const maxEngagements = Math.max(stats?.engagements.given || 0, stats?.engagements.received || 0, 1);
  const givenHeight = stats ? (stats.engagements.given / maxEngagements) * 100 : 0;
  const receivedHeight = stats ? (stats.engagements.received / maxEngagements) * 100 : 0;

  // Glass card style
  const glassCardStyle = {
    background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
    backdropFilter: 'blur(32px) saturate(160%)',
    WebkitBackdropFilter: 'blur(32px) saturate(160%)',
    border: '1px solid rgba(249, 84, 0, 0.15)',
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset'
  };

  // Glass bar style
  const glassBarStyle = {
    background: 'rgba(255, 255, 255, 0.08)',
    borderRadius: '6px',
  };

  const barFillStyle = {
    background: 'linear-gradient(180deg, #ff8c42 0%, #f95400 100%)',
    borderRadius: '6px',
    boxShadow: '0 0 12px rgba(249, 84, 0, 0.4)',
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/80 backdrop-blur-md"
        onClick={onClose}
      />

      {/* Modal */}
      <div
        className="relative w-full max-w-lg max-h-[85vh] flex flex-col animate-slide-up rounded-t-3xl overflow-hidden"
        style={{
          background: 'linear-gradient(180deg, rgba(15, 10, 11, 0.98) 0%, rgba(10, 10, 10, 0.99) 100%)',
          backdropFilter: 'blur(40px)',
          WebkitBackdropFilter: 'blur(40px)',
          border: '1px solid rgba(249, 84, 0, 0.2)',
          borderBottom: 'none',
          boxShadow: '0 -8px 32px rgba(0, 0, 0, 0.8), 0 0 60px rgba(249, 84, 0, 0.05)'
        }}
      >
        {/* Handle */}
        <div className="flex justify-center pt-3 pb-2">
          <div className="w-10 h-1 rounded-full bg-gradient-to-r from-[#f95400]/40 via-[#ff8c42]/60 to-[#f95400]/40" />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-5 pb-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-3">
            <div className="glass-icon glass-icon-md">
              <ChartIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">Your Stats</h2>
              <p className="text-gray-400 text-sm">Lifetime performance</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-9 h-9 rounded-full flex items-center justify-center transition-all active:scale-95"
            style={{
              background: 'rgba(255, 255, 255, 0.05)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
            }}
          >
            <XIconFill className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Loading State - Full modal */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center py-16">
            <div className="w-8 h-8 border-2 border-[#f95400]/30 border-t-[#f95400] rounded-full animate-spin" />
          </div>
        ) : error || !stats ? (
          <div className="flex-1 flex flex-col items-center justify-center py-16">
            <div className="w-16 h-16 rounded-full flex items-center justify-center mb-4" style={{
              background: 'rgba(255, 255, 255, 0.05)',
            }}>
              <XIconFill className="w-8 h-8 text-gray-500" />
            </div>
            <p className="text-gray-400 mb-4">{error || 'Could not load stats'}</p>
            <button onClick={loadStats} className="btn-primary px-6 py-3">Retry</button>
          </div>
        ) : (
          /* Scrollable Content */
          <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-hide">
              {/* Karma Flow & Engagements - Side by Side */}
              <div className="grid grid-cols-2 gap-3">
                {/* Karma Flow */}
                <div className="p-4 pb-3 rounded-2xl" style={glassCardStyle}>
                  <h3 className="flex items-center gap-2 text-xs font-semibold text-gray-300 mb-5">
                    <TrendingUpIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
                    Karma Flow
                  </h3>
                  <div className="flex items-end justify-center gap-8">
                    <div className="flex flex-col items-center">
                      <div className="w-9 h-14 flex items-end rounded-md overflow-hidden" style={glassBarStyle}>
                        <div
                          className="w-full transition-all duration-500"
                          style={{ ...barFillStyle, height: `${earnedHeight}%` }}
                        />
                      </div>
                      <p className="text-[10px] text-gray-400 mt-2">Earned</p>
                      <p className="text-sm font-bold" style={{ color: '#ff8c42' }}>{formatKarma(stats.user.total_credits_earned)}</p>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-9 h-14 flex items-end rounded-md overflow-hidden" style={glassBarStyle}>
                        <div
                          className="w-full transition-all duration-500 opacity-60"
                          style={{ ...barFillStyle, height: `${spentHeight}%` }}
                        />
                      </div>
                      <p className="text-[10px] text-gray-400 mt-2">Spent</p>
                      <p className="text-sm font-bold text-gray-300">{formatKarma(stats.user.total_credits_spent)}</p>
                    </div>
                  </div>
                </div>

                {/* Engagements */}
                <div className="p-4 pb-3 rounded-2xl" style={glassCardStyle}>
                  <h3 className="flex items-center gap-2 text-xs font-semibold text-gray-300 mb-5">
                    <BoltIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
                    Engagements
                  </h3>
                  <div className="flex items-end justify-center gap-8">
                    <div className="flex flex-col items-center">
                      <div className="w-9 h-14 flex items-end rounded-md overflow-hidden" style={glassBarStyle}>
                        <div
                          className="w-full transition-all duration-500"
                          style={{ ...barFillStyle, height: `${givenHeight}%` }}
                        />
                      </div>
                      <p className="text-[10px] text-gray-400 mt-2">Given</p>
                      <p className="text-sm font-bold" style={{ color: '#ff8c42' }}>{stats.engagements.given}</p>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-9 h-14 flex items-end rounded-md overflow-hidden" style={glassBarStyle}>
                        <div
                          className="w-full transition-all duration-500"
                          style={{ ...barFillStyle, height: `${receivedHeight}%` }}
                        />
                      </div>
                      <p className="text-[10px] text-gray-400 mt-2">Received</p>
                      <p className="text-sm font-bold" style={{ color: '#ff8c42' }}>{stats.engagements.received}</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Posts Stats */}
              <div className="p-4 rounded-2xl" style={glassCardStyle}>
                <div className="flex items-center gap-2 mb-4">
                  <SendIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
                  <h3 className="text-sm font-semibold text-gray-300">Your Posts</h3>
                </div>
                <div className="grid grid-cols-3 gap-4">
                  <div className="text-center">
                    <p className="text-2xl font-bold" style={{ color: '#ff8c42' }}>{stats.posts.total}</p>
                    <p className="text-xs text-gray-400">Total</p>
                  </div>
                  <div className="text-center">
                    <p className="text-2xl font-bold" style={{ color: '#ff8c42' }}>{stats.posts.active}</p>
                    <p className="text-xs text-gray-400">Active</p>
                  </div>
                  <div className="text-center">
                    <p className="text-2xl font-bold text-gray-400">{stats.posts.completed}</p>
                    <p className="text-xs text-gray-400">Completed</p>
                  </div>
                </div>
              </div>

              {/* Recent Posts */}
              {stats.recent_posts.length > 0 && (
                <div className="space-y-3 pb-4">
                  <h3 className="text-sm font-semibold text-gray-300 px-1">Recent Posts</h3>
                  {stats.recent_posts.map((post) => (
                    <div key={post.id} className="p-4 rounded-2xl" style={glassCardStyle}>
                      <div className="flex items-center justify-between mb-2">
                        <span
                          className="px-2 py-0.5 rounded-full text-xs font-medium"
                          style={{
                            background: post.status === 'active' ? 'rgba(249, 84, 0, 0.15)' : 'rgba(255, 255, 255, 0.05)',
                            color: post.status === 'active' ? '#ff8c42' : 'rgba(255, 255, 255, 0.5)',
                            border: post.status === 'active' ? '1px solid rgba(249, 84, 0, 0.3)' : '1px solid rgba(255, 255, 255, 0.1)',
                          }}
                        >
                          {post.status}
                        </span>
                        <span className="text-xs text-gray-500">
                          {new Date(post.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <p className="text-sm text-gray-300 truncate mb-3">{post.x_link}</p>
                      <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{
                            background: 'linear-gradient(90deg, #f95400 0%, #ff8c42 100%)',
                            boxShadow: '0 0 8px rgba(249, 84, 0, 0.3)',
                            width: `${post.engagement_progress}%`
                          }}
                        />
                      </div>
                      <p className="text-xs text-gray-500 mt-2">
                        {post.engagement_progress}% complete • {formatKarma(post.escrow_remaining)} karma remaining
                      </p>
                    </div>
                  ))}
                </div>
              )}
          </div>
        )}
      </div>
    </div>
  );
}
