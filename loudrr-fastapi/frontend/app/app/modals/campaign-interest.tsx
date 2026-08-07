'use client';

import { useState } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { api } from '@/lib/api';
import { ICON_GRADIENT_STYLE } from '../shared';
import { CheckIconFill, XIconFill, TargetIconFill } from '../icons';

/**
 * Loudrr Mini App — CampaignInterestModal
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function CampaignInterestModal({
  isOpen,
  onClose,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const options = [
    { id: 'airdrops', label: 'Airdrops' },
    { id: 'token_rewards', label: 'Token Rewards' },
    { id: 'nfts', label: 'NFTs' },
    { id: 'exclusive_access', label: 'Exclusive Access' },
  ];

  const toggleOption = (id: string) => {
    hapticFeedback('light');
    setSelected(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await api.registerFeatureInterest('campaigns', selected);
      onSuccess();
    } catch {
      hapticFeedback('error');
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div
        className="relative w-full max-w-sm rounded-2xl p-5 animate-slide-up"
        style={{
          background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
          backdropFilter: 'blur(32px) saturate(160%)',
          WebkitBackdropFilter: 'blur(32px) saturate(160%)',
          border: '1px solid rgba(249, 84, 0, 0.15)',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <div className="glass-icon glass-icon-sm glass-icon-orange">
              <TargetIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
            </div>
            <h3 className="text-base font-semibold text-white">What interests you?</h3>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-xl flex items-center justify-center transition-colors hover:bg-white/10"
            style={{
              background: 'rgba(255, 255, 255, 0.05)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
            }}
          >
            <XIconFill className="w-4 h-4 text-gray-400" />
          </button>
        </div>

        {/* Options */}
        <div className="space-y-2 mb-5">
          {options.map(option => (
            <button
              key={option.id}
              onClick={() => toggleOption(option.id)}
              className="w-full py-2.5 px-3 rounded-xl text-left transition-all flex items-center justify-between"
              style={{
                background: selected.includes(option.id)
                  ? 'linear-gradient(135deg, rgba(249, 84, 0, 0.15) 0%, rgba(249, 84, 0, 0.05) 100%)'
                  : 'transparent',
                border: selected.includes(option.id)
                  ? '1px solid rgba(249, 84, 0, 0.4)'
                  : '1px solid rgba(255, 255, 255, 0.06)',
              }}
            >
              <span className={`text-sm font-medium ${selected.includes(option.id) ? 'text-[#f95400]' : 'text-white'}`}>
                {option.label}
              </span>
              {selected.includes(option.id) && (
                <CheckIconFill className="w-4 h-4" style={ICON_GRADIENT_STYLE} />
              )}
            </button>
          ))}
        </div>

        {/* Submit */}
        <button
          onClick={handleSubmit}
          disabled={selected.length === 0 || submitting}
          className="w-full h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95 disabled:opacity-50"
          style={{
            background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            border: '1px solid rgba(249, 84, 0, 0.4)',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
            color: 'white',
          }}
        >
          {submitting ? (
            <>
              <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Submitting...
            </>
          ) : (
            'Submit'
          )}
        </button>
      </div>
    </div>
  );
}
