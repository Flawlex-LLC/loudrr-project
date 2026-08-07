/**
 * Loudrr Mini App — Leaf components
 * ==================================
 * Small, self-contained presentational components.
 * Extracted from app/app/page.tsx during the modularization refactor.
 */
'use client';

import React, { useState } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { ICON_GRADIENT_STYLE } from '../shared';
import { FireIconFill, CheckIconFill } from '../icons';

// Logo loader with expanding circle from right edge to full size
export function PixelLoader({ isComplete, size: sizeProp = 'default' }: { isComplete?: boolean; progress?: number; size?: 'default' | 'sm' | 'xs' }) {
  // Size variants: default=72px, sm=32px, xs=20px
  const sizeMap = { default: 72, sm: 32, xs: 20 };
  const size = sizeMap[sizeProp];
  const dotSize = Math.max(2, Math.round(size / 18)); // Scale dot size with loader size
  return (
    <div className="loader-container" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <style>{`
        @keyframes circle-grow-${size} {
          0% {
            width: ${dotSize}px;
            height: ${dotSize}px;
            top: calc(50% - ${dotSize / 2}px);
            right: 0;
            opacity: 0.6;
          }
          100% {
            width: ${size}px;
            height: ${size}px;
            top: 0;
            right: 0;
            opacity: 0;
          }
        }
        @keyframes logo-pulse {
          0% { opacity: 0.6; }
          50% { opacity: 1; }
          100% { opacity: 0.6; }
        }
      `}</style>
      <div style={{ position: 'relative', width: size, height: size }}>
        {/* Base logo */}
        <img
          src="/loudrr-icon.png"
          alt=""
          width={size}
          height={size}
          style={{
            width: size,
            height: size,
            display: 'block',
            animation: isComplete ? 'none' : 'logo-pulse 1.5s ease-in-out infinite',
          }}
        />
        {/* Expanding circle - masked to logo shape */}
        {!isComplete && (
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: size,
              height: size,
              overflow: 'hidden',
              maskImage: 'url(/loudrr-icon.png)',
              maskSize: '100% 100%',
              maskRepeat: 'no-repeat',
              WebkitMaskImage: 'url(/loudrr-icon.png)',
              WebkitMaskSize: '100% 100%',
              WebkitMaskRepeat: 'no-repeat',
            } as React.CSSProperties}
          >
            {/* Circle starts as dot on right, expands to full logo size */}
            <div
              style={{
                position: 'absolute',
                borderRadius: '50%',
                background: 'radial-gradient(circle, rgba(255,255,255,0.5) 0%, rgba(255,255,255,0.25) 50%, rgba(255,255,255,0.1) 100%)',
                mixBlendMode: 'color',
                animation: `circle-grow-${size} 1.5s ease-out infinite`,
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function TabButton({
  icon,
  iconOutline,
  label,
  active,
  onClick,
  tabId,
}: {
  icon: React.ReactNode;
  iconOutline: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  tabId: string;
}) {
  return (
    <button
      onClick={onClick}
      data-tab={tabId}
      className={`relative z-10 flex flex-col items-center justify-center flex-1 px-3 py-2 rounded-2xl transition-all active:scale-95 ${
        active ? 'text-[#f95400]' : 'text-gray-500 hover:text-gray-400'
      }`}
    >
      <div
        className="w-6 h-6 flex items-center justify-center mb-1 transition-all"
        style={{
          filter: active ? 'drop-shadow(0 0 8px rgba(249, 84, 0, 0.6))' : 'none',
        }}
      >
        <div className="w-5 h-5">{active ? icon : iconOutline}</div>
      </div>
      <span className={`text-[9px] font-semibold text-center w-full ${active ? 'text-[#f95400]' : ''}`}>
        {label}
      </span>
    </button>
  );
}

export function StreakCard({ currentStreak }: { currentStreak: number }) {
  const [showInfo, setShowInfo] = useState(false);

  // Get current day of week (0 = Sunday, 1 = Monday, ..., 6 = Saturday)
  const today = new Date().getDay();
  // Convert to Mon-Sun format (0 = Monday, 6 = Sunday)
  const todayMonBased = today === 0 ? 6 : today - 1;

  // Calculate which days of the week the user has engaged
  const daysEngaged = Math.min(currentStreak, todayMonBased + 1);

  const weekDays = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

  return (
    <div className="relative overflow-hidden rounded-xl p-4" style={{
      background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.03) 0%, rgba(15, 10, 11, 0.6) 50%, rgba(249, 84, 0, 0.02) 100%)',
      backdropFilter: 'blur(40px) saturate(180%)',
      WebkitBackdropFilter: 'blur(40px) saturate(180%)',
      border: '1px solid rgba(249, 84, 0, 0.2)',
      boxShadow: '0 8px 32px rgba(0, 0, 0, 0.8), 0 1px 0 rgba(249, 84, 0, 0.08) inset'
    }}>
      {/* Info Button - Top Right */}
      <button
        onClick={() => {
          hapticFeedback('light');
          setShowInfo(!showInfo);
        }}
        className="absolute top-3 right-3 w-5 h-5 rounded-full bg-white/[0.05] flex items-center justify-center hover:bg-white/[0.1] transition-colors z-10"
      >
        <span className="text-[10px] text-gray-500 font-medium">i</span>
      </button>

      {/* Tooltip - Shows on click */}
      {showInfo && (
        <div className="absolute top-10 right-3 w-48 p-2.5 rounded-lg bg-black/95 border border-white/10 shadow-xl z-20 slide-up">
          <p className="text-[10px] text-gray-400 leading-relaxed">
            Engage daily to build streaks. Missing a day resets to 0. Rewards coming soon!
          </p>
        </div>
      )}

      {/* Header Row */}
      <div className="flex items-center gap-3 mb-4">
        <div className={`glass-icon glass-icon-md ${currentStreak > 0 ? 'glass-icon-orange' : ''}`}>
          <FireIconFill className="w-5 h-5" style={currentStreak > 0 ? ICON_GRADIENT_STYLE : { color: 'white' }} />
        </div>
        <div>
          <div className="flex items-baseline gap-1.5">
            <span className={`text-2xl font-bold ${currentStreak > 0 ? 'text-white' : 'text-gray-500'}`}>
              {currentStreak}
            </span>
            <span className="text-sm font-bold text-gray-300">day streak</span>
          </div>
        </div>
      </div>

      {/* Day Progress - Gamified Pills */}
      <div className="flex gap-1.5">
        {weekDays.map((day, index) => {
          const isCompleted = index < daysEngaged;
          const isToday = index === todayMonBased;
          const isFuture = index > todayMonBased;

          return (
            <div key={index} className="flex-1 flex flex-col items-center gap-1">
              <div className={`w-full h-9 rounded-lg flex items-center justify-center transition-all ${
                isCompleted
                  ? 'bg-gradient-to-b from-[#f95400] to-[#ff8c42] shadow-lg shadow-orange-500/30 ring-2 ring-orange-500/20'
                  : isToday
                  ? 'bg-orange-500/15 border-2 border-orange-500/50 border-dashed'
                  : isFuture
                  ? 'bg-white/5 border border-white/10'
                  : 'bg-white/10 border border-white/20'
              }`}>
                {isCompleted && <CheckIconFill className="w-4 h-4 drop-shadow-lg" style={ICON_GRADIENT_STYLE} />}
              </div>
              <span className={`text-[10px] font-bold ${
                isCompleted ? 'text-orange-400' : isToday ? 'text-orange-300' : 'text-gray-400'
              }`}>
                {day}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
