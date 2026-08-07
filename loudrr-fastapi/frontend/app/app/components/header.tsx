'use client';

import { hapticFeedback } from '@/lib/telegram';
import { User } from '@/lib/api';
import { ICON_GRADIENT_STYLE } from '../shared';
import { ChartIconFill, XLogoIcon, ChevronDownIconFill, ChevronRightIconFill, DiscordIcon, TelegramIcon } from '../icons';

/**
 * Loudrr Mini App — Header
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function Header({
  user,
  showProfileMenu,
  setShowProfileMenu,
  onStatsClick,
  onLinkX,
}: {
  user: User | null;
  showProfileMenu: boolean;
  setShowProfileMenu: (show: boolean) => void;
  onStatsClick: () => void;
  onLinkX: () => void;
}) {
  const telegramUsername = user?.telegram_username || 'User';
  const xUsername = user?.x_username || null;

  return (
    <div className="fixed top-0 left-0 right-0 bg-black/90 backdrop-blur-xl border-b border-[#f95400]/20 z-40 tg-safe-area-top">
      <div className="flex items-center justify-between px-4 py-3">
        {/* Logo */}
        <div className="flex items-center">
          <img
            src="/loudrr-logo.png"
            alt="Loudrr"
            className="h-8 w-auto"
          />
        </div>

        {/* Profile */}
        <div className="relative">
          <button
            onClick={() => {
              hapticFeedback('light');
              setShowProfileMenu(!showProfileMenu);
            }}
            className="glass-pill flex items-center gap-2 hover:bg-white/10 transition-all"
          >
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#f95400] to-[#ff7020] flex items-center justify-center">
              <span className="text-xs font-bold text-black">
                {telegramUsername.charAt(0).toUpperCase()}
              </span>
            </div>
            <span className="text-sm text-gray-300 max-w-[100px] truncate">
              {telegramUsername}
            </span>
            <ChevronDownIconFill className={`w-4 h-4 text-gray-400 transition-transform ${showProfileMenu ? 'rotate-180' : ''}`} />
          </button>

          {/* Profile Dropdown */}
          {showProfileMenu && (
            <>
              {/* Backdrop */}
              <div
                className="fixed inset-0 z-40"
                onClick={() => setShowProfileMenu(false)}
              />

              {/* Menu */}
              <div className="absolute right-0 top-full mt-2 w-64 z-50 overflow-hidden slide-up rounded-2xl" style={{
                background: '#0A0A0A',
                border: '1px solid rgba(249, 84, 0, 0.25)',
                boxShadow: '0 8px 32px rgba(0, 0, 0, 0.9), 0 1px 0 rgba(249, 84, 0, 0.1) inset'
              }}>
                {/* Telegram Account */}
                <div className="px-4 py-3 border-b border-white/[0.06]">
                  <div className="flex items-center gap-3">
                    <div className="glass-icon glass-icon-md">
                      <TelegramIcon className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-400">Telegram</p>
                      <p className="text-sm font-medium text-white truncate">@{telegramUsername}</p>
                    </div>
                  </div>
                </div>

                {/* X Account - clickable if not connected */}
                {xUsername ? (
                  <div className="px-4 py-3 border-b border-white/[0.06]">
                    <div className="flex items-center gap-3">
                      <div className="glass-icon glass-icon-md">
                        <XLogoIcon className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-gray-400">X Account</p>
                        <p className="text-sm font-medium text-white truncate">@{xUsername}</p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => {
                      hapticFeedback('light');
                      setShowProfileMenu(false);
                      onLinkX();
                    }}
                    className="w-full px-4 py-3 border-b border-white/[0.06] flex items-center gap-3 hover:bg-white/[0.04] transition-colors"
                  >
                    <div className="glass-icon glass-icon-md">
                      <XLogoIcon className="w-5 h-5 text-white" />
                    </div>
                    <div className="flex-1 min-w-0 text-left">
                      <p className="text-xs text-gray-400">X Account</p>
                      <p className="text-sm text-[#f95400]">Link your account</p>
                    </div>
                    <ChevronRightIconFill className="w-4 h-4 text-gray-500" />
                  </button>
                )}

                {/* Discord */}
                <div className="px-4 py-3 border-b border-white/[0.06]">
                  <div className="flex items-center gap-3">
                    <div className="glass-icon glass-icon-md">
                      <DiscordIcon className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-400">Discord</p>
                      <p className="text-sm text-gray-500 italic">Coming soon</p>
                    </div>
                  </div>
                </div>

                {/* Stats Option */}
                <button
                  onClick={() => {
                    hapticFeedback('light');
                    onStatsClick();
                  }}
                  className="w-full px-4 py-3 flex items-center gap-3 hover:bg-white/[0.04] transition-colors"
                >
                  <div className="glass-icon glass-icon-md">
                    <ChartIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                  </div>
                  <div className="flex-1 text-left">
                    <p className="text-sm font-medium text-white">Stats</p>
                    <p className="text-xs text-gray-400">View your performance</p>
                  </div>
                  <ChevronRightIconFill className="w-4 h-4 text-gray-500" />
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
