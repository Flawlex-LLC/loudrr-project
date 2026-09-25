'use client';

import { useUserPolling } from '../shared';

/**
 * Loudrr Mini App — XVerificationPendingScreen
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function XVerificationPendingScreen({ xUsername, onPoll }: { xUsername?: string; onPoll: () => Promise<void> | void }) {
  useUserPolling(onPoll, 5000);
  return (
    <div className="min-h-screen bg-black flex flex-col items-center justify-center p-6">
      <div className="mb-6">
        <img src="/loudrr-icon.png" alt="Loudrr" className="w-20 h-20" />
      </div>
      <h1 className="text-2xl font-bold text-white mb-2">Under Review</h1>
      <p className="text-gray-400 text-center mb-6 max-w-sm">
        An admin is reviewing your X account verification request.
        {xUsername ? <> We'll let you know the outcome shortly.</> : null}
      </p>

      <div
        className="w-full max-w-sm rounded-2xl p-5 mb-4 flex items-center gap-3"
        style={{
          background: 'rgba(255, 255, 255, 0.04)',
          border: '1px solid rgba(255, 255, 255, 0.08)',
        }}
      >
        <div className="w-3 h-3 rounded-full bg-orange-500 animate-pulse" />
        <span className="text-white text-sm">Pending admin review</span>
      </div>

      <p className="text-gray-600 text-xs text-center max-w-xs">
        Keep this app open or check back later. This usually takes a few hours.
      </p>
    </div>
  );
}
