/**
 * Loudrr Mini App — Shared types, constants, helpers
 * ===================================================
 * Cross-cutting definitions used by multiple screens/components.
 * Extracted from app/app/page.tsx during the modularization refactor.
 */
import React, { useEffect } from 'react';
import type { SessionResponse, CompleteResponse, ClaimBatch } from '@/lib/api';

// ---- Types ----------------------------------------------------------------

export type Tab = 'home' | 'engage' | 'campaigns' | 'earn' | 'loud';

export type EngageState =
  | 'idle' | 'loading' | 'ready' | 'engaging' | 'completing' | 'completed' | 'error';

/** Lifted engage state — persists across tab switches. */
export interface EngageData {
  state: EngageState;
  session: SessionResponse | null;
  currentPostIndex: number;
  engagedPosts: Set<string>;
  error: string | null;
  result: CompleteResponse | null;
  lastFetchedAt: number | null;
  // Queue-based claim history (like spot trading)
  claimHistory: ClaimBatch[];
  hasProcessingBatch: boolean;
  isClaimLoading: boolean;
}

export type WaitlistData = {
  x_username?: string;
  submitted_at?: string;
  referral_code?: string;
};

// ---- Constants ------------------------------------------------------------

export const STALE_THRESHOLD_MS = 20 * 60 * 1000; // 20 minutes

/** Orange gradient style for icons (clipped to text). */
export const ICON_GRADIENT_STYLE = {
  background: 'linear-gradient(135deg, #FF9500 0%, #f95400 50%, #CC5500 100%)',
  WebkitBackgroundClip: 'text',
  WebkitTextFillColor: 'transparent',
  backgroundClip: 'text',
} as React.CSSProperties;

export const REGIONS = [
  { value: 'north_america', label: 'North America' },
  { value: 'europe', label: 'Europe' },
  { value: 'middle_east', label: 'Middle East' },
  { value: 'south_asia', label: 'South Asia' },
  { value: 'southeast_asia', label: 'Southeast Asia' },
  { value: 'east_asia', label: 'East Asia' },
  { value: 'africa', label: 'Africa' },
  { value: 'latin_america', label: 'Latin America' },
  { value: 'oceania', label: 'Oceania' },
  { value: 'cis_eastern_europe', label: 'CIS / Eastern Europe' },
];

export const NICHES = [
  { value: 'memecoins', label: 'Memecoins' },
  { value: 'gamefi', label: 'GameFi' },
  { value: 'trading', label: 'Trading' },
  { value: 'nfts', label: 'NFTs' },
  { value: 'defi', label: 'DeFi' },
  { value: 'ai_tech', label: 'AI / Tech' },
  { value: 'daos', label: 'DAOs' },
];

// ---- Helpers --------------------------------------------------------------

/**
 * Format karma value for display.
 *
 * DECIMAL KARMA SYSTEM:
 * - Backend stores 4 decimal places (e.g., 1.0300)
 * - Frontend displays 2 decimal places (e.g., 1.03)
 * - Whole numbers show as integers (e.g., 150.00 -> 150)
 */
export function formatKarma(value: number): string {
  if (Number.isInteger(value)) {
    return value.toLocaleString();
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Map a TweetScout score to its tier multiplier label. */
export function getScoreMultiplier(score: number): string {
  if (score >= 1000) return "1.35x";
  if (score >= 800) return "1.30x";
  if (score >= 600) return "1.25x";
  if (score >= 400) return "1.20x";
  if (score >= 200) return "1.15x";
  if (score >= 100) return "1.10x";
  return "1.0x";
}

/** Map a TweetScout score to its tier name. */
export function getScoreTier(score: number): string {
  if (score >= 1000) return "GOAT";
  if (score >= 800) return "OG";
  if (score >= 600) return "Legend";
  if (score >= 400) return "Based";
  if (score >= 200) return "Degen";
  if (score >= 100) return "Normie";
  return "Anon";
}

// ---- Hooks ----------------------------------------------------------------

/** Polls `onPoll` on an interval — used by X-verification screens. */
export function useUserPolling(onPoll: () => Promise<void> | void, intervalMs: number = 3000) {
  useEffect(() => {
    const id = setInterval(() => { onPoll(); }, intervalMs);
    return () => clearInterval(id);
  }, [onPoll, intervalMs]);
}
