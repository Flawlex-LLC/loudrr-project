'use client';

import React, { useState, useEffect, useRef } from 'react';
import { hapticFeedback, openLink } from '@/lib/telegram';
import { api, Post, SessionResponse, User, AppSettings } from '@/lib/api';
import { EngageData, STALE_THRESHOLD_MS, ICON_GRADIENT_STYLE, formatKarma } from '../shared';
import { BoltIconFill, PlusIconFill, CheckIconFill, HeartIconFill, XIconFill, ExternalLinkIconFill, InfoIconFill, XLogoIcon, ClockIconFill, SendIconFill } from '../icons';
import { PixelLoader } from '../components/leaf';
import { SubmitModal } from '../modals/submit';

/**
 * Loudrr Mini App — EngageTab
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function EngageTab({
  user,
  onUserUpdate,
  engageData,
  setEngageData,
  settings,
  activeTab,
}: {
  user: User | null;
  onUserUpdate: () => void;
  engageData: EngageData;
  setEngageData: React.Dispatch<React.SetStateAction<EngageData>>;
  settings: AppSettings | null;
  activeTab: string;
}) {
  // Extract state from lifted engageData
  const { state, session, currentPostIndex, engagedPosts, error, result, lastFetchedAt, claimHistory, hasProcessingBatch, isClaimLoading } = engageData;

  // Helper to update specific fields
  const updateEngageData = (updates: Partial<EngageData>) => {
    setEngageData(prev => ({ ...prev, ...updates }));
  };

  // Local state (not persisted across tab switches)
  const [clickedPost, setClickedPost] = useState<string | null>(null);
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const [likeIntentEnabled, setLikeIntentEnabled] = useState(true); // Default ON
  const [refreshing, setRefreshing] = useState(false);
  const [showClaimTooltip, setShowClaimTooltip] = useState(false);
  const [showFailurePopup, setShowFailurePopup] = useState(false);
  const [failedCount, setFailedCount] = useState(0);
  const [earnedKarma, setEarnedKarma] = useState(0); // Karma earned in the batch (shown in popup)
  const [showEngageInfo, setShowEngageInfo] = useState(false);

  // Refs
  const carouselRef = useRef<HTMLDivElement>(null);
  const clickedPostRef = useRef<string | null>(null);
  const sessionRef = useRef<SessionResponse | null>(null);
  const engagedPostsRef = useRef<Set<string>>(new Set());
  const currentPostIndexRef = useRef(0);
  const isScrollingRef = useRef(false); // Flag to disable onScroll during programmatic scroll
  const claimTooltipTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Check if posts are stale (20+ minutes old)
  const isStale = lastFetchedAt && (Date.now() - lastFetchedAt > STALE_THRESHOLD_MS);
  const showMandatoryRefresh = state === 'ready' && isStale && !refreshing;

  // Helper to extract tweet ID and construct like intent URL
  const getLikeIntentUrl = (post: Post): string => {
    // Use tweet_id if available, otherwise extract from URL
    let tweetId = post.tweet_id;
    if (!tweetId) {
      // Extract from URL pattern: x.com/user/status/123456789 or twitter.com/user/status/123456789
      const match = post.x_link.match(/(?:twitter\.com|x\.com)\/\w+\/status\/(\d+)/);
      tweetId = match?.[1] || '';
    }
    if (tweetId) {
      return `https://twitter.com/intent/like?tweet_id=${tweetId}`;
    }
    // Fallback to original URL if can't extract tweet ID
    return post.x_link;
  };

  // Get the appropriate URL based on like intent toggle
  const getEngageUrl = (post: Post): string => {
    return likeIntentEnabled ? getLikeIntentUrl(post) : post.x_link;
  };

  // Keep refs in sync with state
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    engagedPostsRef.current = engagedPosts;
  }, [engagedPosts]);

  // NOTE: currentPostIndexRef is the MAX ALLOWED scroll position (first unengaged post)
  // It should NOT sync with currentPostIndex state (which is the visual position)
  // Only update it on: session start, return from X engagement

  // NOTE: Removed auto-scroll useEffect - all scrolling is now handled explicitly:
  // - handleEngageClick: scrolls to next card on click
  // - startSession: scrolls to first unengaged card on load
  // - handleReturn: scrolls to next card when returning from X
  // This avoids race conditions between multiple scroll triggers.

  // Auto-detect return from X and advance to first unengaged card
  useEffect(() => {
    if (state !== 'engaging') return;

    const handleReturn = () => {
      // User returned from X - use ref to get current clickedPost value
      const postId = clickedPostRef.current;
      if (!postId) return;

      // Tell the backend the user returned from X for this post.
      // Fire-and-forget — purely a signal, never blocks the UI.
      api.verifyReturn(postId).catch(() => { /* non-critical */ });

      // Mark post as engaged (DB already confirmed in handleEngageClick)
      const newEngaged = new Set(engagedPostsRef.current).add(postId);
      engagedPostsRef.current = newEngaged;

      // Clear clicked state
      clickedPostRef.current = null;
      setClickedPost(null);

      // Find first unengaged post
      const posts = sessionRef.current?.posts || [];
      let nextIndex = posts.length; // Default to claim card
      for (let i = 0; i < posts.length; i++) {
        if (!newEngaged.has(posts[i].id)) {
          nextIndex = i;
          break;
        }
      }

      // Update state using lifted state setter
      currentPostIndexRef.current = nextIndex;
      setEngageData(prev => ({
        ...prev,
        state: 'ready',
        engagedPosts: newEngaged,
        currentPostIndex: nextIndex,
      }));

      // FORCE SCROLL after DOM updates (fixes race condition)
      // Disable onScroll handler to prevent it from snapping back
      isScrollingRef.current = true;
      setTimeout(() => {
        if (carouselRef.current) {
          const container = carouselRef.current;
          const cardWidth = container.offsetWidth * 0.8;
          const spacer = container.offsetWidth * 0.1;
          const targetScroll = spacer + (nextIndex * (cardWidth + 12)) - (container.offsetWidth - cardWidth) / 2;
          container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'smooth' });
        }
        hapticFeedback('success');
        // Re-enable onScroll after scroll animation completes
        setTimeout(() => {
          isScrollingRef.current = false;
        }, 400);
      }, 50);
    };

    const handleVisibilityChange = () => {
      // Only run when Engage tab is active to prevent scroll reset during tab switches
      if (document.visibilityState === 'visible' && activeTab === 'engage') {
        handleReturn();
      }
    };

    // Also listen for focus - handles desktop browser tab switching
    const handleFocus = () => {
      // Only run when Engage tab is active
      if (activeTab === 'engage') {
        handleReturn();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleFocus);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleFocus);
    };
  }, [state, setEngageData, activeTab]);

  // Reset isScrollingRef on unmount to prevent stuck state
  useEffect(() => {
    return () => {
      isScrollingRef.current = false;
    };
  }, []);

  // Auto-load posts when entering Engage tab (skip idle screen)
  useEffect(() => {
    if (activeTab === 'engage' && state === 'idle' && user) {
      startSession();
    }
  }, [activeTab, state, user]);

  // NOTE: Scroll lock no longer needed - we only render cards up to maxAllowed + 1
  // so there's nothing to scroll to beyond unlocked cards

  const startSession = async (skipPendingRestore = false) => {
    try {
      updateEngageData({ state: 'loading', error: null, result: null });

      const data = await api.startSession();

      // Check if user has 10+ pending engagements - show verification immediately
      // Skip this check if we're doing a fresh start after claim
      if (data.show_verification && !skipPendingRestore) {
        const pendingSet = new Set(data.pending_post_ids || []);
        engagedPostsRef.current = pendingSet;
        currentPostIndexRef.current = data.posts.length;
        updateEngageData({
          session: data,
          state: 'ready',
          engagedPosts: pendingSet,
          currentPostIndex: data.posts.length,
          lastFetchedAt: Date.now(),
        });
        return;
      }

      if (data.posts.length > 0) {
        // Use pending_post_ids to restore progress, OR empty set for fresh start after claim
        const pendingSet = skipPendingRestore ? new Set<string>() : new Set(data.pending_post_ids || []);
        engagedPostsRef.current = pendingSet;

        // Find first unengaged post (fresh posts are first in array)
        let firstUnengagedIndex = 0;
        for (let i = 0; i < data.posts.length; i++) {
          if (!pendingSet.has(data.posts[i].id)) {
            firstUnengagedIndex = i;
            break;
          }
          // If all posts are pending, go to last post + 1 (check back later)
          if (i === data.posts.length - 1) {
            firstUnengagedIndex = data.posts.length;
          }
        }

        currentPostIndexRef.current = firstUnengagedIndex;
        updateEngageData({
          session: data,
          state: 'ready',
          engagedPosts: pendingSet,
          currentPostIndex: firstUnengagedIndex,
          lastFetchedAt: Date.now(),
        });

        // Scroll to first unengaged card after DOM renders
        console.log('[Session] Started, firstUnengagedIndex:', firstUnengagedIndex, 'totalPosts:', data.posts.length, 'engagedCount:', pendingSet.size);
        if (firstUnengagedIndex > 0) {
          requestAnimationFrame(() => {
            const container = carouselRef.current;
            if (container) {
              isScrollingRef.current = true;
              const cardWidth = container.offsetWidth * 0.8;
              const spacerWidth = container.offsetWidth * 0.1;
              const targetScroll = spacerWidth + (firstUnengagedIndex * (cardWidth + 12)) - (container.offsetWidth - cardWidth) / 2;
              console.log('[Session] Initial scroll to index:', firstUnengagedIndex, 'targetScroll:', Math.round(targetScroll));
              container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'instant' });
              setTimeout(() => { isScrollingRef.current = false; }, 100);
            }
          });
        }
      } else {
        updateEngageData({
          session: data,
          state: 'completed',
          engagedPosts: new Set(),
          currentPostIndex: 0,
          result: {
            success: true,
            message: data.message || 'No posts available right now',
            credits_awarded: 0,
          },
          lastFetchedAt: Date.now(),
        });
      }
    } catch (err) {
      updateEngageData({
        state: 'error',
        error: err instanceof Error ? err.message : 'Failed to start session',
      });
    }
  };

  // Handler for mandatory refresh
  const handleMandatoryRefresh = async () => {
    setRefreshing(true);
    await startSession();
    setRefreshing(false);
  };

  const handleEngageClick = async (post: Post) => {
    // No session_token needed - engagements are tracked at user level
    if (engagedPosts.has(post.id)) return;

    // IMMEDIATE: Open link first - hyperlink speed, no delay
    openLink(getEngageUrl(post));
    hapticFeedback('light'); // After link opens

    // Fire API call in background (don't wait)
    api.recordClick(post.id).catch(err => {
      console.error('[Engage] Failed to record click:', err);
    });

    // Small delay before updating UI (badge, scroll) - let redirect happen first
    setTimeout(() => {
      // Mark as engaged
      const newEngaged = new Set(engagedPosts).add(post.id);
      engagedPostsRef.current = newEngaged;

      // Find next unengaged card
      const posts = session?.posts || [];
      let nextIndex = posts.length;
      for (let i = 0; i < posts.length; i++) {
        if (!newEngaged.has(posts[i].id)) {
          nextIndex = i;
          break;
        }
      }

      console.log('[Engage] Post clicked, finding next unengaged. Current:', currentPostIndex, 'Next:', nextIndex, 'Total:', posts.length);

      // Update refs
      currentPostIndexRef.current = nextIndex;

      // Direct DOM scroll - no React state dependency
      const container = carouselRef.current;
      if (container) {
        isScrollingRef.current = true; // Prevent onScroll interference
        const cardWidth = container.offsetWidth * 0.8;
        const spacerWidth = container.offsetWidth * 0.1;
        const targetScroll = spacerWidth + (nextIndex * (cardWidth + 12)) - (container.offsetWidth - cardWidth) / 2;
        console.log('[Engage] Scrolling to index:', nextIndex, 'targetScroll:', Math.round(targetScroll));
        container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'smooth' });
        setTimeout(() => { isScrollingRef.current = false; }, 350);
      }

      // Update React state for UI (checkmarks, counter)
      setEngageData(prev => ({
        ...prev,
        engagedPosts: newEngaged,
        currentPostIndex: nextIndex,
      }));

      hapticFeedback('success');
    }, 100); // 100ms delay - enough for redirect to start
  };

  // Advance to next card with animation
  const advanceToNextCard = (callback?: () => void) => {
    const nextIndex = currentPostIndex + 1;
    if (nextIndex < (session?.posts?.length || 0)) {
      updateEngageData({ currentPostIndex: nextIndex });
      callback?.();
    } else {
      // All posts done - complete the session
      completeSession();
    }
  };

  // Fetch claim history (for polling and initial load)
  const fetchClaimHistory = async () => {
    try {
      const data = await api.getClaimHistory();
      updateEngageData({
        claimHistory: data.batches,
        hasProcessingBatch: data.has_processing,
      });
      return data;
    } catch (err) {
      console.error('Failed to fetch claim history:', err);
      return null;
    }
  };

  // Poll for claim history updates when there's a processing batch
  useEffect(() => {
    if (!hasProcessingBatch) return;

    const interval = setInterval(async () => {
      const data = await fetchClaimHistory();
      if (data && !data.has_processing) {
        // Processing complete - check results
        const recentBatch = data.batches?.[0];
        const failedNum = recentBatch?.failed ?? 0;
        const creditsEarned = recentBatch?.credits_awarded ?? 0;

        // Always show popup with results (earned + failed)
        setFailedCount(failedNum);
        setEarnedKarma(creditsEarned);
        setShowFailurePopup(true);
        hapticFeedback(failedNum > 0 ? 'error' : 'success');

        // Refresh user data (balance updated)
        onUserUpdate();
      }
    }, 5000); // Poll every 5 seconds

    return () => clearInterval(interval);
  }, [hasProcessingBatch]);

  const completeSession = async () => {
    // Queue-based verification (like spot trading)

    // Prevent double-click
    if (isClaimLoading) return;

    updateEngageData({ isClaimLoading: true });

    try {
      hapticFeedback('medium');

      const data = await api.queueClaim();

      if (!data.success) {
        // Show error (e.g., not enough engagements, time limit)
        updateEngageData({
          state: 'error',
          error: data.message,
          isClaimLoading: false,
        });
        hapticFeedback('error');
        return;
      }

      // Success - verification queued!
      hapticFeedback('success');

      // Immediately clear engaged posts for instant visual feedback
      engagedPostsRef.current = new Set<string>();
      currentPostIndexRef.current = 0;
      updateEngageData({
        engagedPosts: new Set<string>(),
        currentPostIndex: 0,
        hasProcessingBatch: true, // Show "Processing..." immediately
      });

      // Scroll carousel to first card instantly
      if (carouselRef.current) {
        carouselRef.current.scrollTo({ left: 0, behavior: 'instant' });
      }

      // Fetch updated claim history and fresh cards in parallel (background)
      const [historyData, sessionData] = await Promise.all([
        fetchClaimHistory(),
        api.startSession(),
      ]);

      // Update with fresh cards - smooth swap
      updateEngageData({
        state: 'ready',
        session: sessionData,
        claimHistory: historyData?.batches || [],
        hasProcessingBatch: historyData?.has_processing || true,
        isClaimLoading: false,
        lastFetchedAt: Date.now(),
      });

    } catch (err) {
      updateEngageData({
        state: 'error',
        error: err instanceof Error ? err.message : 'Failed to queue verification',
        isClaimLoading: false,
      });
      hapticFeedback('error');
    }
  };

  const currentPost = session?.posts?.[currentPostIndex];
  // Progress is based on engaged posts (persists across sessions), not current feed position
  const progress = (engagedPosts.size / 10) * 100;

  // Mandatory refresh popup - shown when posts are 20+ minutes old
  if (showMandatoryRefresh) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" />
        <div
          className="relative w-full max-w-sm rounded-2xl p-5 animate-slide-up text-center"
          style={{
            background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
            backdropFilter: 'blur(32px) saturate(160%)',
            WebkitBackdropFilter: 'blur(32px) saturate(160%)',
            border: '1px solid rgba(249, 84, 0, 0.15)',
            boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset',
          }}
        >
          {/* Icon */}
          <div className="flex justify-center mb-4">
            <div className="glass-icon glass-icon-md glass-icon-orange">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="url(#iconGradient)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <defs>
                  <linearGradient id="iconGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#f95400" />
                    <stop offset="100%" stopColor="#ff8c42" />
                  </linearGradient>
                </defs>
                <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </div>
          </div>

          {/* Title */}
          <h3 className="text-lg font-semibold mb-2">Posts Outdated</h3>

          {/* Description */}
          <p className="text-gray-400 mb-5 text-sm">
            Your posts are more than 20 minutes old. Refresh to see the latest available posts.
          </p>

          {/* CTA Button */}
          <button
            onClick={handleMandatoryRefresh}
            disabled={refreshing}
            className="w-full h-12 rounded-xl font-medium text-sm transition-all duration-200 flex items-center justify-center"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(249, 84, 0, 0.4)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
              color: 'white',
            }}
          >
            {refreshing ? (
              <span className="flex items-center justify-center gap-2">
                <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Refreshing...
              </span>
            ) : (
              'Refresh Posts'
            )}
          </button>
        </div>
      </div>
    );
  }

  // Idle state
  if (state === 'idle') {
    return (
      <>
        <div className="fixed inset-0 flex flex-col items-center justify-center overflow-hidden bg-black p-4">
          <div className="text-center max-w-sm">
            <div className="w-24 h-24 mx-auto mb-6 rounded-full gold-gradient-bg flex items-center justify-center glow-gold pulse-gold">
              <BoltIconFill className="w-12 h-12 text-black" />
            </div>
            <h2 className="text-2xl font-bold mb-2 gold-gradient-text">Ready to Engage?</h2>
            <p className="text-gray-400 mb-8">
              Engage with posts to earn karma. Each engagement takes about 30 seconds.
            </p>
            <button onClick={() => startSession()} className="btn-primary w-full text-lg py-4">
              Start Engaging
            </button>
            <button
              onClick={() => {
                hapticFeedback('light');
                setShowSubmitModal(true);
              }}
              className="btn-secondary w-full mt-3 flex items-center justify-center gap-2"
            >
              <PlusIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
              Submit Your Post
            </button>
          </div>
        </div>
        <SubmitModal
          isOpen={showSubmitModal}
          onClose={() => setShowSubmitModal(false)}
          user={user}
          onUserUpdate={onUserUpdate}
          settings={settings}
        />
      </>
    );
  }

  // Loading state
  if (state === 'loading') {
    return (
      <div className="fixed inset-0 flex items-center justify-center overflow-hidden bg-black">
        <PixelLoader />
      </div>
    );
  }

  // Error state
  if (state === 'error') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        {/* Backdrop */}
        <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" />

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
          <div className="flex flex-col items-center mb-5">
            <div className="glass-icon glass-icon-md glass-icon-orange mb-4">
              <XIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
            </div>
            <h3 className="text-base font-semibold text-white">Something went wrong</h3>
          </div>

          {/* Error message */}
          <p className="text-xs text-gray-500 text-center mb-5">{error}</p>

          {/* Button */}
          <button
            onClick={() => startSession()}
            className="w-full h-12 rounded-2xl text-sm font-semibold flex items-center justify-center transition-all active:scale-95"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(249, 84, 0, 0.4)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
              color: 'white',
            }}
          >
            Try Again
          </button>
        </div>
      </div>
    );
  }

  // Engaging state - user is on X, will auto-advance when they return
  if (state === 'engaging') {
    return (
      <div className="fixed inset-0 flex items-center justify-center overflow-hidden bg-black p-4">
        <div className="text-center max-w-sm">
          <div className="w-20 h-20 mx-auto mb-6 rounded-full gold-gradient-bg pulse-gold glow-gold flex items-center justify-center">
            <XLogoIcon className="w-10 h-10 text-black" />
          </div>
          <h2 className="text-xl font-bold mb-2">Engaging on X...</h2>
          <p className="text-gray-400 text-sm">Like & reply to the post, then return here</p>
        </div>
      </div>
    );
  }

  // Completing state
  if (state === 'completing') {
    return (
      <div className="fixed inset-0 flex flex-col items-center justify-center overflow-hidden bg-black">
        <PixelLoader />
        <p className="text-gray-400 mt-4">Verifying your engagements...</p>
      </div>
    );
  }

  // Completed state
  if (state === 'completed') {
    const isRetryRequired = result?.retry_required;
    const hasWarning = result?.warning;
    const hasPenalty = (result?.penalty_applied || 0) > 0;

    return (
      <>
        <div className="fixed inset-0 flex items-center justify-center overflow-hidden bg-black p-4">
          <div className="text-center max-w-sm slide-up">
            {isRetryRequired ? (
              // Retry required - verification failed first time
              <>
                <div className="w-24 h-24 mx-auto mb-6 rounded-full bg-yellow-500/20 border border-yellow-500/50 flex items-center justify-center">
                  <InfoIconFill className="w-12 h-12 text-yellow-500" />
                </div>
                <h2 className="text-xl font-bold mb-2">Verification Failed</h2>
                <p className="text-gray-400 mb-4">Please engage with the posts on X (like + reply) and try again.</p>
                {hasWarning && (
                  <p className="text-sm text-yellow-500 mb-4">
                    Warning: Your honesty score dropped to {result?.honesty_score || 9}
                  </p>
                )}
                <button onClick={completeSession} className="btn-primary w-full">
                  Verify Again
                </button>
              </>
            ) : hasPenalty ? (
              // Failed with penalty
              <>
                <div className="w-24 h-24 mx-auto mb-6 rounded-full bg-red-500/20 border border-red-500/50 flex items-center justify-center">
                  <InfoIconFill className="w-12 h-12 text-red-500" />
                </div>
                <h2 className="text-xl font-bold mb-2">Verification Failed</h2>
                <p className="text-gray-400 mb-2">{result?.message}</p>
                <p className="text-sm text-red-400 mb-4">
                  -{result?.penalty_applied} karma penalty. Honesty score: {result?.honesty_score}
                </p>
                <button onClick={() => startSession()} className="btn-primary w-full">
                  Try Again
                </button>
              </>
            ) : result?.success && result.credits_awarded > 0 ? (
              // Success
              <>
                <div className="w-28 h-28 mx-auto mb-6 rounded-full gold-gradient-bg flex items-center justify-center glow-gold">
                  <CheckIconFill className="w-14 h-14 text-black" />
                </div>
                <h2 className="text-4xl font-bold gold-gradient-text stat-glow mb-2">+{formatKarma(result.credits_awarded)}</h2>
                <p className="text-xl text-gray-300 mb-2">Karma Earned!</p>
                {result.passed !== undefined && result.failed !== undefined && result.failed > 0 && (
                  <p className="text-sm text-gray-400 mb-4">
                    {result.passed} verified, {result.failed} need re-engagement
                  </p>
                )}
                <button onClick={() => startSession()} className="btn-primary w-full">
                  Engage More
                </button>
              </>
            ) : (
              // No credits / other - could be failed verifications or no posts
              <>
                <div className="w-24 h-24 mx-auto mb-6 rounded-full bg-black/50 border border-[#f95400]/30 flex items-center justify-center">
                  <InfoIconFill className="w-12 h-12 text-[#f95400]" />
                </div>
                <h2 className="text-xl font-bold mb-2">{result?.message || 'Session Complete'}</h2>
                <p className="text-gray-400 mb-8">Tap below to re-engage and earn karma</p>
                <button onClick={() => startSession()} className="btn-primary w-full">
                  Start Engaging
                </button>
              </>
            )}
            {!isRetryRequired && (
              <button
                onClick={() => {
                  hapticFeedback('light');
                  setShowSubmitModal(true);
                }}
                className="btn-secondary w-full mt-3 flex items-center justify-center gap-2"
              >
                <PlusIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                Submit Your Post
              </button>
            )}
          </div>
        </div>
        <SubmitModal
          isOpen={showSubmitModal}
          onClose={() => setShowSubmitModal(false)}
          user={user}
          onUserUpdate={onUserUpdate}
          settings={settings}
        />
      </>
    );
  }

  // Ready state - show current post
  return (
    <>
      <div className="p-4">
        <div className="mb-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-xl font-bold text-white leading-none">Engage</h2>
              <button
                onClick={() => {
                  hapticFeedback('light');
                  setShowEngageInfo(true);
                }}
                className="w-5 h-5 rounded-full bg-white/[0.05] flex items-center justify-center hover:bg-white/[0.1] transition-colors -mt-0.5"
              >
                <span className="text-[10px] text-gray-500 font-medium leading-none">i</span>
              </button>
            </div>
            <div className="flex items-center gap-2">
              <div
                className="flex items-center gap-1.5 px-3 py-1 rounded-full text-sm"
                style={{
                  background: 'rgba(255, 255, 255, 0.05)',
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                  color: 'rgba(255, 255, 255, 0.6)',
                }}
              >
                <span className="font-semibold">{(session?.posts?.length || 0) - engagedPosts.size}</span>
                <span className="text-xs opacity-70">available</span>
              </div>
              <div
                className="flex items-center gap-1.5 px-3 py-1 rounded-full text-sm"
                style={{
                  background: engagedPosts.size > 0
                    ? 'linear-gradient(135deg, rgba(249, 84, 0, 0.25) 0%, rgba(249, 84, 0, 0.15) 100%)'
                    : 'rgba(255, 255, 255, 0.05)',
                  border: engagedPosts.size > 0 ? '1px solid rgba(249, 84, 0, 0.4)' : '1px solid rgba(255, 255, 255, 0.1)',
                  color: engagedPosts.size > 0 ? '#ff8c42' : 'rgba(255, 255, 255, 0.6)',
                }}
              >
                <span className="font-semibold">{engagedPosts.size}</span>
                <span className="text-xs opacity-70">queue</span>
              </div>
            </div>
          </div>
          <div className="h-2.5 bg-white/10 rounded-full overflow-hidden ring-1 ring-white/20">
            <div
              className="h-full rounded-full transition-all duration-500 relative overflow-hidden"
              style={{
                background: 'linear-gradient(90deg, #f95400 0%, #ff8c42 50%, #f95400 100%)',
                backgroundSize: '200% 100%',
                animation: 'progress-shine 3s ease-in-out infinite',
                boxShadow: '0 0 12px rgba(249, 84, 0, 0.3), 0 1px 0 rgba(255, 140, 66, 0.4) inset',
                width: `${progress}%`
              }}
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">Post {currentPostIndex + 1} of {session?.posts?.length || 0}</p>

          {/* Like Intent Toggle */}
          <div
            className="flex items-center justify-between mt-3 p-3 rounded-xl transition-all"
            style={{
              background: likeIntentEnabled
                ? 'linear-gradient(135deg, rgba(249, 84, 0, 0.08) 0%, rgba(15, 10, 11, 0.6) 100%)'
                : 'linear-gradient(135deg, rgba(255, 255, 255, 0.03) 0%, rgba(15, 10, 11, 0.6) 100%)',
              backdropFilter: 'blur(20px)',
              WebkitBackdropFilter: 'blur(20px)',
              border: likeIntentEnabled ? '1px solid rgba(249, 84, 0, 0.3)' : '1px solid rgba(255, 255, 255, 0.08)',
              boxShadow: likeIntentEnabled ? '0 0 20px rgba(249, 84, 0, 0.1)' : 'none'
            }}
          >
            <div className="flex items-center gap-2">
              <div className="glass-icon glass-icon-sm glass-icon-orange">
                <HeartIconFill className="w-3.5 h-3.5" style={ICON_GRADIENT_STYLE} />
              </div>
              <span className="text-sm font-medium text-white">Quick Like</span>
            </div>
            <button
              onClick={() => {
                hapticFeedback('light');
                setLikeIntentEnabled(!likeIntentEnabled);
              }}
              className={`relative w-11 h-6 rounded-full transition-all duration-200 ${
                likeIntentEnabled ? 'bg-[#f95400] shadow-[0_0_12px_rgba(249,84,0,0.4)]' : 'bg-white/20'
              }`}
            >
              <span
                className={`absolute left-0 top-1 w-4 h-4 rounded-full bg-white shadow-md transition-transform duration-200 ${
                  likeIntentEnabled ? 'translate-x-[24px]' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </div>

        {/* Card Carousel */}
        <div className="overflow-hidden">
          {/* Cards with nav buttons wrapper */}
          <div className="relative">
          {/* Left navigation button */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (currentPostIndex > 0) {
                const newIndex = currentPostIndex - 1;
                updateEngageData({ currentPostIndex: newIndex });
                const container = carouselRef.current;
                if (container) {
                  isScrollingRef.current = true;
                  const cardWidth = container.offsetWidth * 0.8;
                  const spacerWidth = container.offsetWidth * 0.1;
                  const targetScroll = spacerWidth + (newIndex * (cardWidth + 12)) - (container.offsetWidth - cardWidth) / 2;
                  container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'smooth' });
                  setTimeout(() => { isScrollingRef.current = false; }, 350);
                }
              }
            }}
            disabled={currentPostIndex <= 0}
            className="absolute left-2 top-[140px] -translate-y-1/2 z-10 w-10 h-10 rounded-full flex items-center justify-center transition-all active:scale-95"
            style={{
              background: currentPostIndex > 0
                ? 'linear-gradient(135deg, rgba(249, 84, 0, 0.15) 0%, rgba(0, 0, 0, 0.6) 100%)'
                : 'rgba(255, 255, 255, 0.05)',
              backdropFilter: 'blur(12px)',
              WebkitBackdropFilter: 'blur(12px)',
              border: currentPostIndex > 0 ? '1px solid rgba(249, 84, 0, 0.3)' : '1px solid rgba(255, 255, 255, 0.1)',
              color: currentPostIndex > 0 ? '#fff' : 'rgba(255, 255, 255, 0.3)',
              cursor: currentPostIndex <= 0 ? 'not-allowed' : 'pointer',
              boxShadow: currentPostIndex > 0 ? '0 4px 12px rgba(0, 0, 0, 0.4)' : 'none'
            }}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
          </button>

          {/* Right navigation button */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              const maxAllowed = currentPostIndexRef.current;
              if (currentPostIndex < maxAllowed) {
                const newIndex = currentPostIndex + 1;
                updateEngageData({ currentPostIndex: newIndex });
                const container = carouselRef.current;
                if (container) {
                  isScrollingRef.current = true;
                  const cardWidth = container.offsetWidth * 0.8;
                  const spacerWidth = container.offsetWidth * 0.1;
                  const targetScroll = spacerWidth + (newIndex * (cardWidth + 12)) - (container.offsetWidth - cardWidth) / 2;
                  container.scrollTo({ left: Math.max(0, targetScroll), behavior: 'smooth' });
                  setTimeout(() => { isScrollingRef.current = false; }, 350);
                }
              }
            }}
            disabled={currentPostIndex >= currentPostIndexRef.current}
            className="absolute right-2 top-[140px] -translate-y-1/2 z-10 w-10 h-10 rounded-full flex items-center justify-center transition-all active:scale-95"
            style={{
              background: currentPostIndex < currentPostIndexRef.current
                ? 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(0, 0, 0, 0.6) 100%)'
                : 'rgba(255, 255, 255, 0.05)',
              backdropFilter: 'blur(12px)',
              WebkitBackdropFilter: 'blur(12px)',
              border: currentPostIndex < currentPostIndexRef.current ? '1px solid rgba(249, 84, 0, 0.4)' : '1px solid rgba(255, 255, 255, 0.1)',
              color: currentPostIndex < currentPostIndexRef.current ? '#fff' : 'rgba(255, 255, 255, 0.3)',
              cursor: currentPostIndex >= currentPostIndexRef.current ? 'not-allowed' : 'pointer',
              boxShadow: currentPostIndex < currentPostIndexRef.current ? '0 4px 12px rgba(0, 0, 0, 0.4), 0 0 16px rgba(249, 84, 0, 0.15)' : 'none'
            }}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </button>

          <div
            ref={carouselRef}
            className="flex gap-3 overflow-x-auto snap-x snap-mandatory scrollbar-hide py-2"
            style={{
              overscrollBehaviorX: 'contain',  // Prevent momentum overshoot at boundaries
              scrollSnapStop: 'always',         // Force stop at each snap point
            }}
            onScroll={(e) => {
              // Skip if programmatic scrolling is in progress
              if (isScrollingRef.current) return;

              const container = e.currentTarget;
              const cardWidth = container.offsetWidth * 0.8;
              const gap = 12;
              const spacerWidth = container.offsetWidth * 0.1;
              const newIndex = Math.round((container.scrollLeft - spacerWidth) / (cardWidth + gap));

              // Update current index based on scroll position
              if (newIndex >= 0 && newIndex !== currentPostIndex) {
                updateEngageData({ currentPostIndex: newIndex });
              }
            }}
          >
            {/* Left spacer for centering first card */}
            <div className="flex-shrink-0 w-[10%]" />

            {session?.posts?.slice(0, engagedPosts.size + 1).map((post, index) => {
              const isCenter = index === currentPostIndex;
              const isEngaged = engagedPosts.has(post.id);
              const canAccess = index <= currentPostIndex || isEngaged;

              return (
                <div
                  key={post.id}
                  className={`snap-center flex-shrink-0 transition-all duration-300 w-[80%] ${
                    isCenter
                      ? 'scale-100 opacity-100'
                      : isEngaged
                        ? 'scale-95 opacity-70'  // Engaged but not center - slightly brighter
                        : 'scale-95 opacity-50'   // Not engaged, not center - dimmer
                  }`}
                  onMouseDown={(e) => {
                    // Use onMouseDown for faster response (fires before onClick)
                    // Only handle left click on center card for new engagements
                    if (e.button === 0 && isCenter && !isEngaged) {
                      e.preventDefault();
                      handleEngageClick(post);
                    }
                  }}
                  onClick={() => {
                    // Handle already-engaged cards (re-open link) and side card prevention
                    if (isCenter && isEngaged) {
                      openLink(getEngageUrl(post));
                      hapticFeedback('light');
                    }
                    // Side cards: no action
                  }}
                >
                  <div
                    className={`p-5 min-h-[160px] relative transition-all flex flex-col justify-start rounded-2xl overflow-hidden ${
                      isCenter ? 'cursor-pointer' : ''
                    }`}
                    style={{
                      background: isCenter
                        ? 'linear-gradient(135deg, rgba(249, 84, 0, 0.08) 0%, rgba(15, 10, 11, 0.85) 50%, rgba(249, 84, 0, 0.04) 100%)'
                        : 'linear-gradient(135deg, rgba(249, 84, 0, 0.03) 0%, rgba(15, 10, 11, 0.7) 50%, rgba(249, 84, 0, 0.02) 100%)',
                      backdropFilter: 'blur(32px) saturate(160%)',
                      WebkitBackdropFilter: 'blur(32px) saturate(160%)',
                      border: isCenter ? '1px solid rgba(249, 84, 0, 0.4)' : '1px solid rgba(255, 255, 255, 0.08)',
                      boxShadow: isCenter
                        ? '0 8px 32px rgba(0, 0, 0, 0.6), 0 0 20px rgba(249, 84, 0, 0.15), 0 1px 0 rgba(249, 84, 0, 0.1) inset'
                        : '0 4px 20px rgba(0, 0, 0, 0.4)'
                    }}
                  >
                    {/* Top right icons */}
                    <div className="absolute top-4 right-4 z-20 flex items-center gap-2">
                      <XLogoIcon className="w-5 h-5 text-gray-400" />
                      <ExternalLinkIconFill className="w-5 h-5 text-[#f95400]" />
                    </div>

                    {/* Card content */}
                    <div className="flex flex-col gap-3">
                      {/* Author header */}
                      <div className="flex items-center gap-3">
                        {/* Profile picture with engaged indicator */}
                        <div className="relative flex-shrink-0">
                          {(post.tweet_author_avatar || post.creator_avatar) ? (
                            <img
                              src={post.tweet_author_avatar || post.creator_avatar}
                              alt={post.tweet_author_name || post.creator}
                              className={`w-12 h-12 rounded-full object-cover ${
                                isEngaged ? 'ring-2 ring-[#f95400]/50' : ''
                              }`}
                            />
                          ) : (
                            <div className={`w-12 h-12 rounded-full gold-gradient-bg flex items-center justify-center ${
                              isEngaged ? 'ring-2 ring-[#f95400]/50' : ''
                            }`}>
                              <span className="text-lg font-bold text-black">
                                {(post.tweet_author_username || post.creator_x_username || post.creator).charAt(0).toUpperCase()}
                              </span>
                            </div>
                          )}
                          {/* Small clock on profile for pending (queued) posts */}
                          {isEngaged && (
                            <div className="absolute -bottom-0.5 -right-0.5 w-5 h-5 rounded-full bg-[#f95400] flex items-center justify-center border-2 border-black">
                              <ClockIconFill className="w-3 h-3 text-black" />
                            </div>
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="font-semibold text-white truncate">
                              {post.tweet_author_name || post.creator}
                            </p>
                            {post.is_sponsored && (
                              <span className="badge text-xs flex-shrink-0">+XP</span>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            {(post.tweet_author_username || post.creator_x_username) && (
                              <p className="text-sm text-gray-400">
                                @{post.tweet_author_username || post.creator_x_username}
                              </p>
                            )}
                            {(post.tweet_created_at || post.created_at) && (
                              <span className="text-xs text-gray-500">
                                {new Date(post.tweet_created_at || post.created_at!).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Tweet text */}
                      {post.tweet_text && (
                        <p className="text-sm text-gray-200 line-clamp-3 leading-relaxed">
                          {post.tweet_text}
                        </p>
                      )}

                      {/* Expiry indicator */}
                      {post.hours_remaining !== undefined && (
                        <div className="text-xs text-gray-500 mt-auto pt-2 flex items-center gap-1">
                          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                          {post.hours_remaining > 1
                            ? `${Math.round(post.hours_remaining)}h left`
                            : post.hours_remaining > 0
                              ? `${Math.round(post.hours_remaining * 60)}m left`
                              : 'Expiring soon'
                          }
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}

            {/* "Come back later" card at the end */}
            {/* Only show "all caught up" card when ALL posts are engaged */}
            {engagedPosts.size >= (session?.posts?.length || 0) && (
              <div
                className={`snap-center flex-shrink-0 transition-all duration-300 w-[80%] ${
                  currentPostIndex === (session?.posts?.length || 0)
                    ? 'scale-100 opacity-100'
                    : 'scale-95 opacity-50'
                }`}
              >
                <div className={`card-gold p-5 min-h-[160px] flex flex-col items-center justify-center text-center ${
                  currentPostIndex === (session?.posts?.length || 0) ? 'ring-2 ring-[#f95400]/50' : ''
                }`}>
                  <div className="w-16 h-16 rounded-full bg-white/10 flex items-center justify-center mb-4">
                    <ClockIconFill className="w-8 h-8 text-gray-400" />
                  </div>
                  <h3 className="text-lg font-semibold text-white mb-2">You're all caught up!</h3>
                  <p className="text-gray-400 text-sm">Come back later for more posts to engage with</p>
                </div>
              </div>
            )}

            {/* Right spacer for centering last card */}
            <div className="flex-shrink-0 w-[10%]" />
          </div>
          </div>{/* End of cards + nav buttons wrapper */}

          {/* Scroll indicators - only show for rendered cards */}
          <div className="flex justify-center gap-1.5 mt-3">
            {session?.posts?.slice(0, engagedPosts.size + 1).map((_, index) => (
              <div
                key={index}
                className={`h-1.5 rounded-full transition-all ${
                  index === currentPostIndex
                    ? 'w-6 bg-[#f95400]'
                    : engagedPosts.size > index
                      ? 'w-1.5 bg-[#f95400]/50'
                      : 'w-1.5 bg-gray-600'
                }`}
              />
            ))}
            {/* Show end indicator only when all posts engaged */}
            {engagedPosts.size >= (session?.posts?.length || 0) && (
              <div
                className={`h-1.5 rounded-full transition-all ${
                  currentPostIndex === session?.posts?.length
                    ? 'w-6 bg-[#f95400]'
                    : 'w-1.5 bg-[#f95400]/50'
                }`}
              />
            )}
          </div>

          {/* Action Buttons - Claim and Submit side by side */}
          <div className="mt-4 flex gap-3 relative">
            <button
              onClick={() => {
                if (engagedPosts.size >= 10 && !isClaimLoading && !hasProcessingBatch) {
                  hapticFeedback('medium');
                  completeSession();
                } else if (engagedPosts.size < 10) {
                  hapticFeedback('error');
                  // Show tooltip
                  if (claimTooltipTimeoutRef.current) {
                    clearTimeout(claimTooltipTimeoutRef.current);
                  }
                  setShowClaimTooltip(true);
                  claimTooltipTimeoutRef.current = setTimeout(() => {
                    setShowClaimTooltip(false);
                  }, 2000);
                }
              }}
              disabled={engagedPosts.size < 10 || isClaimLoading || hasProcessingBatch}
              className="flex-1 h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95"
              style={{
                background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
                backdropFilter: 'blur(16px)',
                WebkitBackdropFilter: 'blur(16px)',
                border: '1px solid rgba(249, 84, 0, 0.4)',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
                color: 'white',
              }}
            >
              {isClaimLoading ? (
                <>
                  <ClockIconFill className="w-5 h-5 text-white" />
                  Queuing...
                </>
              ) : hasProcessingBatch ? (
                <>
                  <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Processing...
                </>
              ) : (
                <>
                  <BoltIconFill className="w-5 h-5 text-white" />
                  {engagedPosts.size >= 10 ? 'Claim' : `${engagedPosts.size}/10`}
                </>
              )}
            </button>
            <button
              onClick={() => {
                hapticFeedback('light');
                setShowSubmitModal(true);
              }}
              className="flex-1 h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95"
              style={{
                background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
                backdropFilter: 'blur(16px)',
                WebkitBackdropFilter: 'blur(16px)',
                border: '1px solid rgba(249, 84, 0, 0.4)',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
                color: 'white',
              }}
            >
              <SendIconFill className="w-5 h-5 text-white" />
              Submit
            </button>

            {/* Claim tooltip message */}
            <div
              className={`absolute -top-12 left-0 right-0 flex justify-center transition-all duration-300 ${
                showClaimTooltip ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2 pointer-events-none'
              }`}
            >
              <div
                className="px-4 py-2 rounded-xl text-sm text-white/90"
                style={{
                  background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(15, 10, 11, 0.9) 100%)',
                  backdropFilter: 'blur(16px)',
                  border: '1px solid rgba(249, 84, 0, 0.3)',
                }}
              >
                Minimum 10 queues to claim
              </div>
            </div>
          </div>

          {/* Claim History - shows queued verification batches */}
          <div className="mt-4">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-medium text-gray-400">Verification Queue</h4>
              {hasProcessingBatch && (
                <span className="flex items-center gap-1.5 text-xs text-yellow-500">
                  <span className="w-1.5 h-1.5 bg-yellow-500 rounded-full animate-pulse" />
                  Processing
                </span>
              )}
            </div>
            <div className="glass-card p-3 space-y-2 max-h-48 overflow-y-auto">
              {claimHistory.length > 0 ? (
                claimHistory.slice(0, 5).map((batch) => (
                  <div
                    key={batch.id}
                    className="flex items-center justify-between p-2.5 bg-white/[0.03] rounded-xl border border-white/[0.05]"
                  >
                    <div className="flex items-center gap-2">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${
                        batch.status === 'completed' && batch.credits_awarded && batch.credits_awarded > 0
                          ? 'bg-[#f95400]/20'
                          : batch.status === 'pending' || batch.status === 'processing'
                          ? 'bg-yellow-500/20'
                          : 'bg-white/[0.05]'
                      }`}>
                        {(batch.status === 'pending' || batch.status === 'processing') ? (
                          <ClockIconFill className="w-4 h-4 text-yellow-500" />
                        ) : batch.status === 'completed' && batch.credits_awarded && batch.credits_awarded > 0 ? (
                          <CheckIconFill className="w-4 h-4 text-[#f95400]" />
                        ) : (
                          <InfoIconFill className="w-4 h-4 text-gray-500" />
                        )}
                      </div>
                      <div>
                        <p className="text-xs text-white">{batch.engagement_count} engagements</p>
                        <p className="text-[10px] text-gray-500">
                          {new Date(batch.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </p>
                      </div>
                    </div>
                    <div className="text-right">
                      {(batch.status === 'pending' || batch.status === 'processing') && (
                        <span className="text-yellow-500 text-xs">Verifying...</span>
                      )}
                      {batch.status === 'completed' && batch.credits_awarded !== null && batch.credits_awarded > 0 && (
                        <span className="text-[#f95400] text-sm font-semibold">
                          +{Number(batch.credits_awarded).toFixed(2)}
                        </span>
                      )}
                      {batch.status === 'completed' && (batch.credits_awarded === null || batch.credits_awarded === 0) && (
                        <span className="text-gray-500 text-xs">{batch.failed || 0} failed</span>
                      )}
                      {batch.status === 'failed' && (
                        <span className="text-red-400 text-xs">Error</span>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-center py-4">
                  <div className="glass-icon glass-icon-md mx-auto mb-2">
                    <ClockIconFill className="w-5 h-5 text-gray-500" />
                  </div>
                  <p className="text-xs text-gray-500">No recent claims</p>
                  <p className="text-[10px] text-gray-600 mt-1">Engage with 10 posts to claim</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <SubmitModal
        isOpen={showSubmitModal}
        onClose={() => setShowSubmitModal(false)}
        user={user}
        onUserUpdate={onUserUpdate}
        settings={settings}
      />

      {/* Verification Results Popup - shown when verification completes */}
      {showFailurePopup && (
        <div className="fixed inset-0 bg-black/90 backdrop-blur-md flex items-center justify-center z-50 p-4">
          <div className="w-full max-w-sm bg-black/95 border border-[#f95400]/30 rounded-2xl p-6 text-center shadow-2xl shadow-black/50">
            {/* Icon */}
            <div className={`w-20 h-20 mx-auto mb-4 rounded-full flex items-center justify-center ${
              failedCount > 0 ? 'bg-yellow-500/20' : 'bg-[#f95400]/20'
            }`}>
              {failedCount > 0 ? (
                <InfoIconFill className="w-10 h-10 text-yellow-500" />
              ) : (
                <CheckIconFill className="w-10 h-10 text-[#f95400]" />
              )}
            </div>

            {/* Title */}
            <h3 className="text-xl font-bold mb-2">
              {failedCount > 0 ? 'Verification Complete' : 'All Verified!'}
            </h3>

            {/* Earned Karma - always show if earned */}
            {earnedKarma > 0 && (
              <div className="mb-4">
                <span className="text-3xl font-bold gold-gradient-text">+{formatKarma(earnedKarma)}</span>
                <span className="text-gray-400 ml-2">karma earned</span>
              </div>
            )}

            {/* Failed count message */}
            {failedCount > 0 && (
              <p className="text-gray-400 mb-4 text-sm">
                {failedCount} post{failedCount !== 1 ? 's' : ''} couldn't be verified and {failedCount !== 1 ? 'have' : 'has'} been removed.
              </p>
            )}

            {/* Continue button */}
            <button
              onClick={async () => {
                setShowFailurePopup(false);
                // Light refresh - fetch new cards without full loading state
                try {
                  const data = await api.startSession();
                  if (data.posts.length > 0) {
                    engagedPostsRef.current = new Set<string>();
                    currentPostIndexRef.current = 0;
                    updateEngageData({
                      session: data,
                      engagedPosts: new Set<string>(),
                      currentPostIndex: 0,
                      lastFetchedAt: Date.now(),
                    });
                    // Scroll to first card
                    if (carouselRef.current) {
                      carouselRef.current.scrollTo({ left: 0, behavior: 'instant' });
                    }
                  }
                } catch (err) {
                  console.error('Failed to refresh cards:', err);
                }
              }}
              className="btn-primary w-full"
            >
              Continue Engaging
            </button>
          </div>
        </div>
      )}

      {/* Engage Info Modal */}
      {showEngageInfo && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/80 backdrop-blur-sm"
            onClick={() => setShowEngageInfo(false)}
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
                  <BoltIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
                </div>
                <h3 className="text-base font-semibold text-white">What is Karma?</h3>
              </div>
              <button
                onClick={() => setShowEngageInfo(false)}
                className="w-8 h-8 rounded-xl flex items-center justify-center transition-colors hover:bg-white/10"
                style={{
                  background: 'rgba(255, 255, 255, 0.05)',
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                }}
              >
                <XIconFill className="w-4 h-4 text-gray-400" />
              </button>
            </div>

            {/* Content */}
            <div className="space-y-3 mb-5">
              <p className="text-sm text-gray-400">
                <span className="text-[#f95400] font-medium">1.</span> <span className="text-white font-medium">Boost your posts</span> by spending karma to get engagements
              </p>
              <p className="text-sm text-gray-400">
                <span className="text-[#f95400] font-medium">2.</span> <span className="text-white font-medium">Join USDT raffles</span> by using karma in Earn section, requires engagement on XP posts (Partner posts)
              </p>
              <p className="text-sm text-gray-400">
                <span className="text-[#f95400] font-medium">3.</span> <span className="text-white font-medium">Join token giveaways</span> on the Earn section
              </p>
            </div>

            {/* Note */}
            <p className="text-xs text-gray-500 text-center">
              Engage with posts to earn <span className="text-[#f95400]">karma</span>, the currency of Loudrr
            </p>
          </div>
        </div>
      )}
    </>
  );
}
