'use client';

import { useState } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { api, User } from '@/lib/api';

/**
 * Loudrr Mini App — OnboardingScreen
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function OnboardingScreen({
  user,
  onComplete,
}: {
  user: User;
  onComplete: () => Promise<void>;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleStart = async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await api.completeOnboarding();
      hapticFeedback('success');

      // Refetch user to get updated data
      await onComplete();
    } catch (err: any) {
      setError(err.message || 'Something went wrong');
      hapticFeedback('error');
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-black flex flex-col items-center justify-center p-6">
      {/* Logo */}
      <div className="mb-6">
        <img
          src="/loudrr-icon.png"
          alt="Loudrr"
          className="w-20 h-20"
        />
      </div>

      {/* Welcome Message */}
      <h1 className="text-3xl font-bold text-white mb-2 text-center">
        Welcome to Loudrr!
      </h1>

      <p className="text-gray-400 text-center mb-8 max-w-sm">
        Your multiplier is connected to your X account score.<br />
        Higher score = More karma per engagement.
      </p>

      {/* X Username Display */}
      {user.x_username && (
        <div className="mb-8 px-6 py-3 rounded-xl bg-white/5 border border-[#f95400]/30">
          <span className="text-[#f95400] font-medium">@{user.x_username}</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <p className="text-red-400 text-sm mb-4">{error}</p>
      )}

      {/* Start Button */}
      <button
        onClick={handleStart}
        disabled={loading}
        className="px-8 py-4 btn-primary text-lg flex items-center gap-2"
      >
        {loading ? (
          <>
            <span className="w-5 h-5 border-2 border-black/30 border-t-black rounded-full animate-spin" />
            Loading...
          </>
        ) : (
          <>Let's Go Loudrr</>
        )}
      </button>

      {/* Footer */}
      <p className="mt-8 text-gray-600 text-sm text-center">
        Earn karma by engaging.<br />
        Spend karma to grow.
      </p>
    </div>
  );
}
