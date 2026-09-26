/**
 * Telegram Web App utilities
 */

// Telegram WebApp types
interface TelegramWebApp {
  initData: string;
  initDataUnsafe: {
    user?: {
      id: number;
      first_name: string;
      last_name?: string;
      username?: string;
      language_code?: string;
    };
    start_param?: string;
  };
  colorScheme: 'light' | 'dark';
  themeParams: {
    bg_color?: string;
    text_color?: string;
    hint_color?: string;
    link_color?: string;
    button_color?: string;
    button_text_color?: string;
  };
  isExpanded: boolean;
  viewportHeight: number;
  viewportStableHeight: number;
  MainButton: {
    text: string;
    color: string;
    textColor: string;
    isVisible: boolean;
    isActive: boolean;
    isProgressVisible: boolean;
    setText: (text: string) => void;
    onClick: (callback: () => void) => void;
    offClick: (callback: () => void) => void;
    show: () => void;
    hide: () => void;
    enable: () => void;
    disable: () => void;
    showProgress: (leaveActive?: boolean) => void;
    hideProgress: () => void;
  };
  BackButton: {
    isVisible: boolean;
    onClick: (callback: () => void) => void;
    offClick: (callback: () => void) => void;
    show: () => void;
    hide: () => void;
  };
  HapticFeedback: {
    impactOccurred: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft') => void;
    notificationOccurred: (type: 'error' | 'success' | 'warning') => void;
    selectionChanged: () => void;
  };
  close: () => void;
  expand: () => void;
  ready: () => void;
  // try_browser: Bot API 7.6+, undocumented; clients ignore names they can't honor
  openLink: (url: string, options?: { try_instant_view?: boolean; try_browser?: string }) => void;
  platform?: string; // "android" | "ios" | "tdesktop" | "macos" | "weba" | ...
  showPopup: (params: {
    title?: string;
    message: string;
    buttons?: Array<{
      id?: string;
      type?: 'default' | 'ok' | 'close' | 'cancel' | 'destructive';
      text?: string;
    }>;
  }, callback?: (buttonId: string) => void) => void;
  showAlert: (message: string, callback?: () => void) => void;
  showConfirm: (message: string, callback?: (confirmed: boolean) => void) => void;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp: TelegramWebApp;
    };
  }
}

/**
 * Get Telegram WebApp instance
 */
export function getTelegramWebApp(): TelegramWebApp | null {
  if (typeof window !== 'undefined' && window.Telegram?.WebApp) {
    return window.Telegram.WebApp;
  }
  return null;
}

/**
 * Check if running inside Telegram
 */
export function isTelegramWebApp(): boolean {
  return getTelegramWebApp() !== null;
}

/**
 * Initialize Telegram WebApp
 */
export function initTelegramWebApp() {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.ready();
    tg.expand();

    // Capture referral start_param (t.me/<bot>/app?startapp=ref_<code>)
    // BEFORE any router.replace drops query params. Kept in localStorage too:
    // sessionStorage dies with the WebView, and applicants often leave for X
    // (OAuth) and come back to a fresh one. The registration screen reads
    // 'loudrr_ref' and validates it.
    try {
      const sp = (window as any)?.Telegram?.WebApp?.initDataUnsafe?.start_param;
      if (typeof sp === 'string') {
        const match = sp.match(/^ref_([A-Za-z0-9_-]{4,16})$/);
        if (match) {
          sessionStorage.setItem('loudrr_ref', match[1]);
          localStorage.setItem('loudrr_ref', match[1]);
        }
      }
    } catch {
      // storage unavailable — referral attribution is best-effort.
    }
  }
}

/**
 * Get user from Telegram WebApp
 */
export function getTelegramUser() {
  const tg = getTelegramWebApp();
  return tg?.initDataUnsafe?.user || null;
}

/**
 * Trigger haptic feedback
 */
export function hapticFeedback(type: 'light' | 'medium' | 'heavy' | 'success' | 'error' | 'warning' | 'selection') {
  const tg = getTelegramWebApp();
  if (!tg) return;

  switch (type) {
    case 'light':
    case 'medium':
    case 'heavy':
      tg.HapticFeedback.impactOccurred(type);
      break;
    case 'success':
    case 'error':
    case 'warning':
      tg.HapticFeedback.notificationOccurred(type);
      break;
    case 'selection':
      tg.HapticFeedback.selectionChanged();
      break;
  }
}

/**
 * Open external link
 */
export function openLink(url: string) {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.openLink(url);
  } else {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}

/**
 * Open X's sign-in (OAuth authorize) page in a way that works on phones.
 *
 * A tapped x.com link on a phone is handed to the X app, and the X app can't
 * run third-party sign-in ("flow name login is currently inaccessible"). Apps
 * only take over links a user taps, not script redirects, so this opens our
 * /x-login page, which forwards to X by script. On Android it also asks
 * Telegram for Chrome, a real browser where X sign-in works.
 */
export function openXLogin(authorizeUrl: string) {
  const hop = `${window.location.origin}/x-login#u=${encodeURIComponent(authorizeUrl)}`;
  const tg = getTelegramWebApp();
  if (tg) {
    tg.openLink(hop, tg.platform === 'android' ? { try_browser: 'chrome' } : undefined);
  } else {
    window.open(hop, '_blank', 'noopener,noreferrer');
  }
}

/**
 * Close the Mini App
 */
export function closeMiniApp() {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.close();
  }
}

/**
 * Show main button
 */
export function showMainButton(text: string, onClick: () => void) {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.MainButton.setText(text);
    tg.MainButton.onClick(onClick);
    tg.MainButton.show();
  }
}

/**
 * Hide main button
 */
export function hideMainButton() {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.MainButton.hide();
  }
}

/**
 * Show back button
 */
export function showBackButton(onClick: () => void) {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.BackButton.onClick(onClick);
    tg.BackButton.show();
  }
}

/**
 * Hide back button
 */
export function hideBackButton() {
  const tg = getTelegramWebApp();
  if (tg) {
    tg.BackButton.hide();
  }
}
