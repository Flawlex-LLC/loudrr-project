'use client';

import { useState, useEffect } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { User, loudApi, LoudProject, LoudProjectsResponse, LoudLeaderboardResponse } from '@/lib/api';
import { ICON_GRADIENT_STYLE } from '../shared';
import { PlusIcon, FireIconFill } from '../icons';
import { PixelLoader } from '../components/leaf';
import { LoudSubmitModal } from '../modals/loud-submit';

/**
 * Loudrr Mini App — LoudTab
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function LoudTab({ user: _user }: { user: User | null }) {
  const [projectsData, setProjectsData] = useState<LoudProjectsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<LoudProject | null>(null);
  const [leaderboardData, setLeaderboardData] = useState<LoudLeaderboardResponse | null>(null);
  const [loadingLeaderboard, setLoadingLeaderboard] = useState(false);
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const [successToast, setSuccessToast] = useState<{ points: number; rank: number } | null>(null);

  useEffect(() => {
    loadProjects();
  }, []);

  useEffect(() => {
    // Auto-select first project on load
    if (projectsData?.projects.length && !selectedProject) {
      setSelectedProject(projectsData.projects[0]);
    }
  }, [projectsData]);

  useEffect(() => {
    // Load leaderboard when project is selected
    if (selectedProject) {
      loadLeaderboard(selectedProject.slug);
    }
  }, [selectedProject]);

  const loadProjects = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await loudApi.getProjects();
      setProjectsData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load projects');
    } finally {
      setLoading(false);
    }
  };

  const loadLeaderboard = async (slug: string) => {
    try {
      setLoadingLeaderboard(true);
      const data = await loudApi.getLeaderboard(slug);
      setLeaderboardData(data);
    } catch (err) {
      console.error('Failed to load leaderboard:', err);
    } finally {
      setLoadingLeaderboard(false);
    }
  };

  const formatTimeRemaining = (hours: number): string => {
    if (hours < 24) return `${hours}h left`;
    const days = Math.floor(hours / 24);
    return `${days}d left`;
  };

  if (loading) {
    return (
      <div className="p-4 flex items-center justify-center min-h-[400px]">
        <PixelLoader size="sm" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4">
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-center">
          <p className="text-red-400">{error}</p>
          <button
            onClick={loadProjects}
            className="mt-2 px-4 py-2 bg-red-500/20 rounded-lg text-red-400 text-sm"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!projectsData || projectsData.projects.length === 0) {
    return (
      <div className="p-5">
        <div className="rounded-2xl bg-[#1a1a1a] p-8 text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-white/5 flex items-center justify-center">
            <FireIconFill className="w-8 h-8 text-gray-600" />
          </div>
          <h3 className="text-lg font-semibold text-white mb-2">No Active Campaigns</h3>
          <p className="text-gray-500 text-sm">
            Check back soon for UGC reward opportunities.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-full pb-32">
      {/* Success Toast */}
      {successToast && (
        <div className="fixed top-4 inset-x-4 z-50 animate-slide-down">
          <div className="relative overflow-hidden rounded-xl border border-[#f95400]/30 bg-[#f95400] shadow-lg shadow-[#f95400]/30">
            <div className="flex items-center gap-3 px-4 py-3">
              <div className="w-10 h-10 rounded-full bg-black/20 flex items-center justify-center flex-shrink-0">
                <svg className="w-6 h-6 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-bold text-black text-lg">+{successToast.points} points!</p>
                <p className="text-black/70 text-sm">Now ranked #{successToast.rank}</p>
              </div>
              <button
                onClick={() => setSuccessToast(null)}
                className="p-1 rounded-full hover:bg-black/10 transition-colors"
              >
                <svg className="w-5 h-5 text-black/60" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Welcome Header */}
      <div className="px-5 pt-5 pb-3">
        <h1 className="text-2xl font-bold text-white">UGC Rewards</h1>
      </div>

      {/* Daily Challenge Card - Multi-layer design matching Home tab */}
      <div className="px-5 mb-5">
        <div className="relative overflow-hidden rounded-2xl border border-[#f95400]/30 bg-gradient-to-br from-[#f95400]/15 via-zinc-900/50 to-black">
          {/* Grid Pattern */}
          <div className="absolute inset-0 opacity-[0.08]" style={{
            backgroundImage: `linear-gradient(#f95400 1px, transparent 1px), linear-gradient(90deg, #f95400 1px, transparent 1px)`,
            backgroundSize: '20px 20px'
          }} />
          {/* Scan Line */}
          <div className="absolute inset-0 bg-gradient-to-b from-transparent via-[#f95400]/[0.04] to-transparent" />

          <div className="relative z-10 p-5">
            {/* Header Row */}
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="glass-icon glass-icon-md glass-icon-orange">
                  <FireIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                </div>
                <div>
                  <p className="text-sm text-[#f95400] uppercase tracking-wider">Daily Submissions</p>
                  <p className="text-xs text-white/60">UGC Rewards</p>
                </div>
              </div>
              <button
                onClick={() => {
                  hapticFeedback('medium');
                  setShowSubmitModal(true);
                }}
                disabled={projectsData.daily_submissions_remaining === 0}
                className="px-4 py-2 rounded-full flex items-center gap-2 transition-all active:scale-95 disabled:opacity-50"
                style={{
                  background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.3) 0%, rgba(255, 140, 66, 0.2) 50%, rgba(249, 84, 0, 0.25) 100%)',
                  backdropFilter: 'blur(16px)',
                  border: '1px solid rgba(249, 84, 0, 0.5)',
                  boxShadow: '0 4px 16px rgba(0, 0, 0, 0.4), 0 1px 0 rgba(255, 140, 66, 0.2) inset'
                }}
              >
                <span className="text-sm font-bold text-white">Submit</span>
                <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
              </button>
            </div>

            {/* Main Display */}
            <div className="mb-4">
              <div className="flex items-baseline gap-2">
                <span className="text-4xl font-bold text-white">{projectsData.daily_submissions_remaining}</span>
                <span className="text-lg text-gray-400">/ {projectsData.daily_limit} remaining</span>
              </div>
            </div>

            {/* Progress Bar with Shine */}
            <div className="h-2.5 bg-white/10 rounded-full overflow-hidden ring-1 ring-white/10">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{
                  width: `${(projectsData.daily_submissions_remaining / projectsData.daily_limit) * 100}%`,
                  background: 'linear-gradient(90deg, #f95400 0%, #ff8c42 50%, #f95400 100%)',
                  backgroundSize: '200% 100%',
                  animation: 'progress-shine 3s ease-in-out infinite',
                  boxShadow: '0 0 12px rgba(249, 84, 0, 0.4)'
                }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Horizontal Scrolling Project Cards */}
      <div className="mb-5">
        <div className="flex items-center justify-between px-5 mb-3">
          <h2 className="text-lg font-semibold text-white">Active Campaigns</h2>
          <span className="text-sm text-gray-500">{projectsData.projects.length} active</span>
        </div>

        <div className="flex gap-3 overflow-x-auto pb-2 px-5 scrollbar-hide">
          {projectsData.projects.map((project) => {
            const isSelected = selectedProject?.id === project.id;
            const isEligible = projectsData.user_tweetscout_score >= project.min_tweetscout_score;

            return (
              <button
                key={project.id}
                onClick={() => {
                  hapticFeedback('light');
                  setSelectedProject(project);
                }}
                className={`relative flex-shrink-0 w-[160px] rounded-2xl p-4 text-left transition-all overflow-hidden ${
                  isSelected
                    ? 'bg-white/[0.08] backdrop-blur-xl border border-[#f95400]/50 shadow-lg shadow-[#f95400]/20'
                    : 'bg-white/[0.03] backdrop-blur-md border border-white/[0.06] hover:bg-white/[0.06] hover:border-white/10'
                }`}
              >
                {/* Grid pattern for selected */}
                {isSelected && (
                  <div className="absolute inset-0 opacity-[0.06]" style={{
                    backgroundImage: `linear-gradient(#f95400 1px, transparent 1px), linear-gradient(90deg, #f95400 1px, transparent 1px)`,
                    backgroundSize: '16px 16px'
                  }} />
                )}

                {/* Content */}
                <div className="relative z-10">
                  {/* Project Logo */}
                  <div className={`w-12 h-12 rounded-xl flex items-center justify-center overflow-hidden mb-3 ${
                    isSelected ? 'bg-[#f95400]/20 ring-1 ring-[#f95400]/30' : 'bg-white/10'
                  }`}>
                    {project.logo_url ? (
                      <img src={project.logo_url} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <span className={`text-lg font-bold ${isSelected ? 'text-[#f95400]' : 'text-white'}`}>
                        {project.name.charAt(0)}
                      </span>
                    )}
                  </div>

                  {/* Project Name */}
                  <h3 className="font-semibold text-sm text-white mb-1 truncate">{project.name}</h3>

                  {/* Reward */}
                  {project.reward_pool && (
                    <p className="text-xs font-medium text-[#f95400] mb-2">{project.reward_pool}</p>
                  )}

                  {/* Time + Eligibility */}
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-gray-500">{formatTimeRemaining(project.time_remaining_hours)}</span>
                    {project.min_tweetscout_score > 0 && !isEligible && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">
                        {project.min_tweetscout_score}+
                      </span>
                    )}
                  </div>
                </div>

                {/* Your rank badge */}
                {project.your_rank && (
                  <div className="absolute -top-1 -right-1 px-2 py-0.5 rounded-full bg-[#f95400] text-[10px] font-bold text-black z-20">
                    #{project.your_rank}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Leaderboard Section */}
      {selectedProject && (
        <div className="px-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Leaderboard</h2>
            <span className="text-sm text-gray-500">Top 50</span>
          </div>

          <div className="relative overflow-hidden rounded-2xl border border-[#f95400]/20 bg-gradient-to-br from-black via-zinc-900/50 to-black">
            {/* Grid Pattern */}
            <div className="absolute inset-0 opacity-[0.05]" style={{
              backgroundImage: `linear-gradient(#f95400 1px, transparent 1px), linear-gradient(90deg, #f95400 1px, transparent 1px)`,
              backgroundSize: '20px 20px'
            }} />
            {/* Scan Line */}
            <div className="absolute inset-0 bg-gradient-to-b from-transparent via-[#f95400]/[0.03] to-transparent" />

            <div className="relative z-10">
            {loadingLeaderboard ? (
              <div className="flex justify-center py-8">
                <PixelLoader size="xs" />
              </div>
            ) : leaderboardData?.leaderboard && leaderboardData.leaderboard.length > 0 ? (
              <div className="max-h-[400px] overflow-y-auto">
                {leaderboardData.leaderboard.slice(0, 50).map((entry, index) => {
                  const isCurrentUser = leaderboardData.user_entry?.user_id === entry.user.id;
                  const isTopThree = entry.rank <= 3;

                  return (
                    <div
                      key={entry.user.id}
                      className={`relative flex items-center gap-3 px-4 py-3 ${
                        index !== 0 ? 'border-t border-white/[0.06]' : ''
                      } ${isCurrentUser ? 'bg-[#f95400]/10' : 'hover:bg-white/[0.02]'}`}
                    >
                      {/* Grid on current user row */}
                      {isCurrentUser && (
                        <div className="absolute inset-0 opacity-[0.05]" style={{
                          backgroundImage: `linear-gradient(#f95400 1px, transparent 1px), linear-gradient(90deg, #f95400 1px, transparent 1px)`,
                          backgroundSize: '12px 12px'
                        }} />
                      )}

                      {/* Rank with Medal Badges */}
                      <div className="relative z-10 w-8 flex items-center justify-center">
                        {entry.rank === 1 ? (
                          <span className="text-xl">🥇</span>
                        ) : entry.rank === 2 ? (
                          <span className="text-xl">🥈</span>
                        ) : entry.rank === 3 ? (
                          <span className="text-xl">🥉</span>
                        ) : (
                          <span className={`text-sm font-semibold ${
                            isCurrentUser ? 'text-[#f95400]' : 'text-gray-500'
                          }`}>
                            {entry.rank}
                          </span>
                        )}
                      </div>

                      {/* Avatar */}
                      <div className={`relative z-10 w-10 h-10 rounded-full flex items-center justify-center overflow-hidden flex-shrink-0 ${
                        isCurrentUser ? 'ring-2 ring-[#f95400]' : ''
                      } bg-white/10`}>
                        {entry.user.avatar ? (
                          <img src={entry.user.avatar} alt="" className="w-full h-full object-cover" />
                        ) : (
                          <span className="text-sm font-medium text-white">
                            {entry.user.display_name?.charAt(0) || '?'}
                          </span>
                        )}
                      </div>

                      {/* Name */}
                      <div className="relative z-10 flex-1 min-w-0">
                        <p className={`font-medium truncate ${isCurrentUser ? 'text-[#f95400]' : 'text-white'}`}>
                          {entry.user.x_username ? `@${entry.user.x_username}` : entry.user.display_name}
                          {isCurrentUser && <span className="ml-2 text-xs bg-[#f95400] text-black px-1.5 py-0.5 rounded font-bold">YOU</span>}
                        </p>
                        <p className="text-xs text-gray-500">{entry.submission_count} posts</p>
                      </div>

                      {/* Points */}
                      <span className={`relative z-10 text-sm font-bold ${isCurrentUser ? 'text-[#f95400]' : 'text-white'}`}>
                        {entry.total_points.toLocaleString()}
                      </span>
                    </div>
                  );
                })}

                {/* User's entry if not in top 50 */}
                {leaderboardData.user_entry && leaderboardData.user_entry.rank && leaderboardData.user_entry.rank > 50 && (
                  <div className="flex items-center gap-3 px-4 py-3 bg-[#f95400]/10 border-t border-[#f95400]/30">
                    <span className="w-8 text-sm font-semibold text-[#f95400]">
                      {leaderboardData.user_entry.rank}
                    </span>
                    <div className="w-10 h-10 rounded-full bg-[#f95400]/20 flex items-center justify-center ring-2 ring-[#f95400]">
                      <span className="text-sm font-bold text-[#f95400]">Y</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-[#f95400]">
                        You <span className="ml-2 text-xs bg-[#f95400] text-black px-1.5 py-0.5 rounded font-bold">YOU</span>
                      </p>
                      <p className="text-xs text-[#f95400]/60">{leaderboardData.user_entry.submission_count} posts</p>
                    </div>
                    <span className="text-sm font-bold text-[#f95400]">
                      {leaderboardData.user_entry.total_points.toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-10">
                <div className="w-14 h-14 mx-auto mb-3 rounded-full bg-white/[0.06] backdrop-blur border border-white/[0.08] flex items-center justify-center">
                  <FireIconFill className="w-7 h-7 text-gray-600" />
                </div>
                <p className="text-gray-400 font-medium">No submissions yet</p>
                <p className="text-gray-600 text-sm mt-1">Be the first to earn points!</p>
              </div>
            )}
            </div>
          </div>
        </div>
      )}

      {/* Floating Action Button */}
      <button
        onClick={() => {
          hapticFeedback('medium');
          setShowSubmitModal(true);
        }}
        disabled={projectsData.daily_submissions_remaining === 0}
        className={`fixed bottom-24 right-4 z-40 w-14 h-14 rounded-full flex items-center justify-center transition-all ${
          projectsData.daily_submissions_remaining > 0
            ? 'glass-icon glass-icon-orange float-animation glow-pulse shadow-lg shadow-[#f95400]/20 hover:scale-105 active:scale-95'
            : 'glass-icon opacity-50 cursor-not-allowed'
        }`}
      >
        <PlusIcon className={`w-7 h-7 ${
          projectsData.daily_submissions_remaining > 0 ? 'text-[#f95400]' : 'text-gray-600'
        }`} />
      </button>

      {/* Submit Modal */}
      {showSubmitModal && (
        <LoudSubmitModal
          projects={projectsData.projects}
          projectsData={projectsData}
          preselectedProject={selectedProject}
          onClose={() => setShowSubmitModal(false)}
          onSuccess={(result) => {
            setShowSubmitModal(false);
            // Show success toast
            setSuccessToast({ points: result.points_awarded ?? 0, rank: result.new_rank ?? 0 });
            setTimeout(() => setSuccessToast(null), 4000);
            // Refresh data
            loadProjects();
            if (selectedProject) {
              loadLeaderboard(selectedProject.slug);
            }
          }}
        />
      )}
    </div>
  );
}
