'use client';

import { StarIconFill } from '../icons';

/**
 * Loudrr Mini App — EarnTab
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function EarnTab() {
  return (
    <div className="p-4 flex flex-col items-center justify-center min-h-[60vh]">
      <div className="text-center max-w-sm">
        <div className="w-24 h-24 mx-auto mb-6 rounded-full bg-gradient-to-br from-[#f95400]/20 to-[#CC5500]/20 border border-[#f95400]/30 flex items-center justify-center">
          <StarIconFill className="w-12 h-12 text-[#f95400]/60" />
        </div>
        <h2 className="text-2xl font-bold mb-2 gold-gradient-text">Earn Rewards</h2>
        <p className="text-gray-400 mb-4">
          Participate in giveaways and burn karma for rewards.
        </p>
        <div className="px-4 py-2 rounded-full bg-[#f95400]/10 border border-[#f95400]/20">
          <span className="text-sm text-[#f95400] font-medium">Coming Soon</span>
        </div>
      </div>
    </div>
  );
}
