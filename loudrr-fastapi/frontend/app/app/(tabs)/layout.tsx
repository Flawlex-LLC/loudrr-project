'use client';

/**
 * (tabs)/layout.tsx — shared shell for the 5 main-app tabs.
 *
 * Stays mounted across tab navigation, so lifted state (engageData, user,
 * settings) is preserved exactly like the old single-component design.
 * Holds: data loading, X-verification gate, onboarding fallback, Header,
 * bottom tab bar, and the Stats / Link-X modals.
 *
 * Child routes: home / engage / campaigns / earn / loud — each a thin page
 * that pulls shared state from TabContext.
 */
import { useEffect, useState, useRef } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { api, User, AppSettings } from '@/lib/api';
import { initTelegramWebApp, hapticFeedback, getTelegramWebApp } from '@/lib/telegram';
import {
  HomeIconFill, HomeIcon, BoltIconFill, BoltIcon,
  TargetIconFill, TargetIcon, StarIconFill, StarIcon, FireIconFill, FireIcon,
} from '../icons';
import { EngageData } from '../shared';
import { PixelLoader, TabButton } from '../components/leaf';
import { Header } from '../components/header';
import { LinkXModal } from '../modals/link-x';
import { ConnectXScreen } from '../screens/connect-x';
import { XMismatchPromptScreen } from '../screens/x-mismatch-prompt';
import { XVerificationPendingScreen } from '../screens/x-verification-pending';
import { OnboardingScreen } from '../screens/onboarding';
import { TabContext } from './tab-context';

export default function TabsLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  // active tab derived from the URL: /app/engage -> "engage"
  const activeTab = pathname.split('/').pop() || 'home';

  const [user, setUser] = useState<User | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingProgress, setLoadingProgress] = useState(0);
  const [showLoader, setShowLoader] = useState(true);
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const [showLinkXModal, setShowLinkXModal] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [comingSoonToast, setComingSoonToast] = useState<string | null>(null);
  const [toastVisible, setToastVisible] = useState(false);
  const comingSoonTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const showComingSoonToast = (message: string) => {
    if (comingSoonTimeoutRef.current) clearTimeout(comingSoonTimeoutRef.current);
    setComingSoonToast(message);
    setToastVisible(true);
    comingSoonTimeoutRef.current = setTimeout(() => {
      setToastVisible(false);
      setTimeout(() => setComingSoonToast(null), 400);
    }, 1500);
  };

  // Lifted engage state — persists across tab navigation (layout stays mounted)
  const [engageData, setEngageData] = useState<EngageData>({
    state: 'idle',
    session: null,
    currentPostIndex: 0,
    engagedPosts: new Set(),
    error: null,
    result: null,
    lastFetchedAt: null,
    claimHistory: [],
    hasProcessingBatch: false,
    isClaimLoading: false,
  });

  useEffect(() => {
    initTelegramWebApp();
    loadInitialData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadInitialData = async () => {
    try {
      setServerError(null);
      const tg = getTelegramWebApp();
      const host = typeof window !== 'undefined' ? window.location.hostname : '';
      const isDev = host === 'localhost' || host.startsWith('dev-app.');
      if (!tg?.initData && !isDev) {
        router.replace('/waitlist');
        return;
      }
      try {
        const [userData, settingsData] = await Promise.all([
          api.getUser(),
          api.getSettings(),
        ]);
        setUser(userData);
        setSettings(settingsData);
      } catch {
        // No account / still on waitlist
        router.replace('/waitlist');
        return;
      }
    } catch (err) {
      console.error('Failed to load initial data:', err);
      setServerError(err instanceof Error ? err.message : 'Failed to connect to server');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!loading) {
      setLoadingProgress(100);
      return;
    }
    const interval = setInterval(() => {
      setLoadingProgress((prev) => (prev >= 90 ? 90 : prev + 3));
    }, 80);
    return () => clearInterval(interval);
  }, [loading]);

  useEffect(() => {
    if (!loading) {
      const timer = setTimeout(() => setShowLoader(false), 400);
      return () => clearTimeout(timer);
    }
  }, [loading]);

  useEffect(() => {
    if (!loading && user && !user.x_username && !user.is_whitelisted) {
      setShowLinkXModal(true);
    }
  }, [loading, user]);

  const loadUser = async () => {
    try {
      const userData = await api.getUser();
      setUser(userData);
    } catch (err) {
      console.error('Failed to load user:', err);
    } finally {
      setLoading(false);
    }
  };

  const goToTab = (tab: string) => {
    hapticFeedback('light');
    if (tab === 'home') loadUser();
    router.push(`/app/${tab}`);
  };

  if (showLoader) {
    return <PixelLoader isComplete={!loading} progress={loadingProgress} />;
  }

  if (serverError) {
    return (
      <div className="h-screen flex flex-col items-center justify-center p-6 bg-black">
        <div className="text-center max-w-sm">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-red-500/10 border border-red-500/30 flex items-center justify-center">
            <svg className="w-8 h-8 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-white mb-2">Server Error</h2>
          <p className="text-gray-400 text-sm mb-6">
            Unable to connect to the server. Please try again later.
          </p>
          <button
            onClick={() => {
              setLoading(true);
              setShowLoader(true);
              loadInitialData();
            }}
            className="px-6 py-3 bg-[#f95400] text-black font-semibold rounded-xl hover:bg-[#ff7020] transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // X verification gate
  if (user && user.is_whitelisted && user.x_verified === false) {
    if (user.x_verification_pending_review) {
      return <XVerificationPendingScreen xUsername={user.x_username || undefined} onPoll={loadUser} />;
    }
    if (user.pending_claimed_x_username) {
      return (
        <XMismatchPromptScreen
          submittedUsername={user.x_username || ''}
          claimedUsername={user.pending_claimed_x_username}
          onResolved={loadUser}
        />
      );
    }
    return <ConnectXScreen xUsername={user.x_username || ''} onPoll={loadUser} />;
  }

  // Onboarding fallback
  if (user && user.is_whitelisted && !user.tweetscout_last_updated && user.x_username) {
    return (
      <OnboardingScreen
        user={user}
        onComplete={async () => {
          await loadUser();
        }}
      />
    );
  }

  return (
    <TabContext.Provider
      value={{ user, settings, loadUser, engageData, setEngageData, activeTab, showComingSoonToast }}
    >
      <div className="h-screen flex flex-col overflow-hidden tg-safe-area-top tg-safe-area-bottom">
        <Header
          user={user}
          showProfileMenu={showProfileMenu}
          setShowProfileMenu={setShowProfileMenu}
          onStatsClick={() => {
            setShowProfileMenu(false);
            router.push('/app/stats');
          }}
          onLinkX={() => setShowLinkXModal(true)}
        />

        <div className="flex-1 overflow-y-auto pb-20 pt-14 scrollbar-content">
          {children}
        </div>

        {comingSoonToast && (
          <div
            className={`fixed bottom-28 left-0 right-0 flex justify-center z-50 pointer-events-none transition-all duration-400 ease-out ${
              toastVisible ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-6'
            }`}
          >
            <div className="px-4 py-2.5 rounded-full text-sm text-white/80 text-center whitespace-pre-line bg-black/70 backdrop-blur-sm">
              {comingSoonToast}
            </div>
          </div>
        )}

        {/* Bottom Tab Bar */}
        <div className="fixed bottom-0 left-0 right-0 flex justify-center px-6 tg-safe-area-bottom pointer-events-none">
          <div
            className="relative rounded-3xl p-2 pointer-events-auto"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.1) 0%, rgba(15, 10, 11, 0.92) 50%, rgba(249, 84, 0, 0.08) 100%)',
              backdropFilter: 'blur(40px) saturate(180%)',
              WebkitBackdropFilter: 'blur(40px) saturate(180%)',
              border: '1px solid rgba(249, 84, 0, 0.3)',
              boxShadow: '0 8px 32px rgba(0, 0, 0, 0.8), 0 0 40px rgba(249, 84, 0, 0.08), 0 1px 0 rgba(249, 84, 0, 0.15) inset'
            }}
          >
            <div className="flex items-center gap-1">
              <TabButton
                tabId="home"
                icon={<HomeIconFill />}
                iconOutline={<HomeIcon />}
                label="Home"
                active={activeTab === 'home'}
                onClick={() => goToTab('home')}
              />
              <TabButton
                tabId="engage"
                icon={<BoltIconFill />}
                iconOutline={<BoltIcon />}
                label="Engage"
                active={activeTab === 'engage'}
                onClick={() => goToTab('engage')}
              />
              <TabButton
                tabId="campaigns"
                icon={<TargetIconFill className="w-6 h-6" />}
                iconOutline={<TargetIcon className="w-6 h-6" />}
                label="Campaigns"
                active={activeTab === 'campaigns'}
                onClick={() => goToTab('campaigns')}
              />
              <TabButton
                tabId="earn"
                icon={<StarIconFill />}
                iconOutline={<StarIcon />}
                label="Earn"
                active={false}
                onClick={() => {
                  hapticFeedback('light');
                  showComingSoonToast('Earn is coming soon.\nKarma will play main role here.');
                }}
              />
              <TabButton
                tabId="loud"
                icon={<FireIconFill />}
                iconOutline={<FireIcon />}
                label="Loud"
                active={!!(user?.loud_access && activeTab === 'loud')}
                onClick={() => {
                  if (user?.loud_access) {
                    goToTab('loud');
                  } else {
                    hapticFeedback('light');
                    showComingSoonToast('LOUD Campaigns will be launching soon!');
                  }
                }}
              />
            </div>
          </div>
        </div>

        {/* Stats is now its own route — /app/stats — not a layout modal. */}

        <LinkXModal
          isOpen={showLinkXModal}
          onClose={() => setShowLinkXModal(false)}
          onSuccess={(data) => {
            setUser(prev => prev ? {
              ...prev,
              x_username: data.x_username,
              tweetscout_score: data.tweetscout_score,
              x_followers_count: data.followers_count,
              x_display_name: data.display_name,
            } : null);
            setShowLinkXModal(false);
          }}
        />
      </div>
    </TabContext.Provider>
  );
}
