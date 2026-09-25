'use client';

import { useState, useEffect } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { api } from '@/lib/api';
import { XLogoIcon } from '../icons';

/**
 * Loudrr Mini App — LinkXModal
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

interface LinkXResult {
  x_username: string;
  tweetscout_score: number;
  tier: string;
  followers_count: number;
  display_name: string;
}

export function LinkXModal({
  isOpen,
  onClose,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (data: LinkXResult) => void;
}) {
  const [username, setUsername] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!username.trim()) {
      setError('Please enter your X username');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const result = await api.linkXAccount(username);
      hapticFeedback('success');
      onSuccess(result);
    } catch (err: any) {
      setError(err.message || 'Failed to link account');
      hapticFeedback('error');
    } finally {
      setLoading(false);
    }
  };

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setUsername('');
      setError('');
      setLoading(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop - no onClick, can't dismiss */}
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" />

      {/* Modal */}
      <div className="relative w-full max-w-sm bg-zinc-900/95 backdrop-blur-xl rounded-2xl border border-[#f95400]/30 p-6 animate-slide-up">
        {/* Header */}
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center">
            <XLogoIcon className="w-5 h-5 text-white" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Link X Account</h2>
            <p className="text-xs text-gray-400">Required to continue</p>
          </div>
        </div>

        <p className="text-sm text-gray-400 mb-4">
          Enter your X username to verify post ownership and display your mindshare score.
        </p>

        {/* Input */}
        <div className="relative mb-4">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">@</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value.replace(/[@\s]/g, ''))}
            placeholder="username"
            className="w-full bg-black/50 border border-gray-700 rounded-xl py-3 pl-8 pr-4 text-white placeholder-gray-500 focus:border-[#f95400]/50 focus:outline-none transition-colors"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !loading) {
                handleSubmit();
              }
            }}
          />
        </div>

        {error && (
          <p className="text-sm text-red-400 mb-4">{error}</p>
        )}

        {/* Single button - no cancel option */}
        <button
          onClick={handleSubmit}
          disabled={loading || !username.trim()}
          className="w-full py-3 rounded-xl bg-[#f95400] text-black font-semibold disabled:opacity-50 hover:bg-[#E56000] transition-colors"
        >
          {loading ? 'Verifying...' : 'Link Account'}
        </button>
      </div>
    </div>
  );
}
