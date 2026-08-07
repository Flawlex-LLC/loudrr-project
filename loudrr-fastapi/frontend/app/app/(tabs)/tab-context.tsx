'use client';

/**
 * TabContext — shared state for the (tabs) route group.
 *
 * The (tabs)/layout.tsx holds the Header, bottom tab bar, lifted engageData,
 * user, and settings. Because Next.js layouts can't pass props to child page
 * routes directly, this context bridges that state down to each tab page
 * (home / engage / campaigns / earn / loud).
 *
 * The layout stays mounted across tab navigation, so engageData and user
 * persist exactly like the previous single-component design.
 */
import { createContext, useContext } from 'react';
import type { User, AppSettings } from '@/lib/api';
import type { EngageData } from '../shared';

export interface TabContextValue {
  user: User | null;
  settings: AppSettings | null;
  loadUser: () => Promise<void>;
  engageData: EngageData;
  setEngageData: React.Dispatch<React.SetStateAction<EngageData>>;
  activeTab: string;
  showComingSoonToast: (message: string) => void;
}

export const TabContext = createContext<TabContextValue | null>(null);

export function useTabContext(): TabContextValue {
  const ctx = useContext(TabContext);
  if (!ctx) {
    throw new Error('useTabContext must be used within the (tabs) layout');
  }
  return ctx;
}
