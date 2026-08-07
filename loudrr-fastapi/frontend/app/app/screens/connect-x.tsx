'use client';

import { useState } from 'react';
import { hapticFeedback, openLink } from '@/lib/telegram';
import { api } from '@/lib/api';
import { ICON_GRADIENT_STYLE, useUserPolling } from '../shared';
import { XLogoIcon } from '../icons';

/**
 * Loudrr Mini App — ConnectXScreen
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function ConnectXScreen({ xUsername, onPoll }: { xUsername: string; onPoll: () => Promise<void> | void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useUserPolling(onPoll);

  const handleConnect = async () => {
    setLoading(true);
    setError(null);
    try {
      const { authorize_url } = await api.startXOAuth();
      hapticFeedback('light');
      openLink(authorize_url);
    } catch (e: any) {
      setError(e?.message || 'Failed to start verification');
      hapticFeedback('error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-black flex flex-col items-center justify-center p-6">
      <div className="mb-6">
        <img src="/loudrr-icon.png" alt="Loudrr" className="w-20 h-20" />
      </div>
      <h1 className="text-2xl font-bold text-white mb-2">You're approved 🎉</h1>
      <p className="text-gray-400 text-center mb-6 max-w-sm">
        Connect your X account to verify it's really you and start earning karma.
      </p>

      <div
        className="w-full max-w-sm rounded-2xl p-5 mb-6"
        style={{
          background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
          backdropFilter: 'blur(32px)',
          border: '1px solid rgba(249, 84, 0, 0.15)',
        }}
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="glass-icon glass-icon-md glass-icon-orange pointer-events-none">
            <XLogoIcon className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
          </div>
          <div>
            <div className="text-white font-medium">Verify @{xUsername}</div>
            <div className="text-gray-500 text-xs">We'll open X in your browser</div>
          </div>
        </div>

        {error && <p className="text-red-400 text-sm mb-3 text-center">{error}</p>}

        <button
          onClick={handleConnect}
          disabled={loading}
          className="w-full h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95 disabled:opacity-50"
          style={{
            background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
            border: '1px solid rgba(249, 84, 0, 0.4)',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
            color: 'white',
          }}
        >
          {loading ? (
            <>
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Opening X…
            </>
          ) : (
            <>Connect X to get started</>
          )}
        </button>
      </div>

      <p className="text-gray-600 text-xs text-center max-w-xs">
        After authorizing on X, return to Telegram. We'll detect it automatically.
      </p>
    </div>
  );
}
