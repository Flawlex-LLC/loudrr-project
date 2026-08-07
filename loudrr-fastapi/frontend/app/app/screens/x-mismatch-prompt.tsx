'use client';

import { useState } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { api } from '@/lib/api';

/**
 * Loudrr Mini App — XMismatchPromptScreen
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function XMismatchPromptScreen({
  submittedUsername,
  claimedUsername,
  onResolved,
}: {
  submittedUsername: string;
  claimedUsername: string;
  onResolved: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState<'yes' | 'no' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleYes = async () => {
    setBusy('yes');
    setError(null);
    try {
      await api.confirmXMismatch();
      hapticFeedback('success');
      await onResolved();
    } catch (e: any) {
      setError(e?.message || 'Failed to submit');
      hapticFeedback('error');
    } finally {
      setBusy(null);
    }
  };

  const handleNo = async () => {
    setBusy('no');
    setError(null);
    try {
      await api.cancelXMismatch();
      hapticFeedback('light');
      await onResolved();
    } catch (e: any) {
      setError(e?.message || 'Failed to cancel');
      hapticFeedback('error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="min-h-screen bg-black flex flex-col items-center justify-center p-6">
      <div className="mb-6">
        <img src="/loudrr-icon.png" alt="Loudrr" className="w-20 h-20" />
      </div>
      <h1 className="text-2xl font-bold text-white mb-2">Different account?</h1>
      <p className="text-gray-400 text-center mb-6 max-w-sm">
        You're approved with <span className="text-white">@{submittedUsername}</span> but you connected{' '}
        <span className="text-[#f95400]">@{claimedUsername}</span>.
      </p>

      <div
        className="w-full max-w-sm rounded-2xl p-5 mb-4"
        style={{
          background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
          backdropFilter: 'blur(32px)',
          border: '1px solid rgba(249, 84, 0, 0.15)',
        }}
      >
        <p className="text-white text-sm text-center mb-4">
          Is <span className="text-[#f95400] font-semibold">@{claimedUsername}</span> your actual account?
        </p>

        {error && <p className="text-red-400 text-sm mb-3 text-center">{error}</p>}

        <div className="flex gap-3">
          <button
            onClick={handleNo}
            disabled={!!busy}
            className="flex-1 h-12 rounded-2xl text-sm font-semibold flex items-center justify-center transition-all active:scale-95 disabled:opacity-50"
            style={{
              background: 'rgba(255, 255, 255, 0.04)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: 'rgba(255, 255, 255, 0.7)',
            }}
          >
            {busy === 'no' ? '…' : 'No, retry'}
          </button>
          <button
            onClick={handleYes}
            disabled={!!busy}
            className="flex-1 h-12 rounded-2xl text-sm font-semibold flex items-center justify-center transition-all active:scale-95 disabled:opacity-50"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
              border: '1px solid rgba(249, 84, 0, 0.4)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
              color: 'white',
            }}
          >
            {busy === 'yes' ? '…' : 'Yes, that\'s me'}
          </button>
        </div>
      </div>

      <p className="text-gray-600 text-xs text-center max-w-xs">
        If yes, an admin will review and approve. If no, you'll go back to Connect X to try again with the correct account.
      </p>
    </div>
  );
}
