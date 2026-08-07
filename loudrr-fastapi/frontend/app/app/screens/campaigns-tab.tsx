'use client';

import { useState, useEffect } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { api, User } from '@/lib/api';
import { ICON_GRADIENT_STYLE } from '../shared';
import { CheckIconFill, TargetIconFill } from '../icons';
import { CampaignInterestModal } from '../modals/campaign-interest';

/**
 * Loudrr Mini App — CampaignsTab
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function CampaignsTab({ user }: { user: User | null }) {
  const [showInterestModal, setShowInterestModal] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(true);

  // Check if user already registered interest
  useEffect(() => {
    const checkRegistration = async () => {
      if (!user) {
        setCheckingStatus(false);
        return;
      }
      try {
        const response = await api.getFeatureInterest('campaigns');
        setRegistered(response.registered);
      } catch {
        // Ignore errors
      } finally {
        setCheckingStatus(false);
      }
    };
    checkRegistration();
  }, [user]);

  const glassCardStyle = {
    background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
    backdropFilter: 'blur(32px) saturate(160%)',
    WebkitBackdropFilter: 'blur(32px) saturate(160%)',
    border: '1px solid rgba(249, 84, 0, 0.15)',
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset'
  };

  return (
    <div className="flex items-center justify-center min-h-[70vh] p-4">
      {/* Popup Card */}
      <div
        className="w-full max-w-sm rounded-2xl p-5"
        style={{
          background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.04) 0%, rgba(15, 10, 11, 0.8) 50%, rgba(249, 84, 0, 0.02) 100%)',
          backdropFilter: 'blur(32px) saturate(160%)',
          WebkitBackdropFilter: 'blur(32px) saturate(160%)',
          border: '1px solid rgba(249, 84, 0, 0.15)',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(249, 84, 0, 0.08) inset',
        }}
      >
        {/* Header */}
        <div className="flex flex-col items-center mb-5">
          <div className="glass-icon glass-icon-lg glass-icon-orange mb-4">
            <TargetIconFill className="w-6 h-6" style={ICON_GRADIENT_STYLE} />
          </div>
          <h3 className="text-base font-semibold text-white mb-1">Campaigns</h3>
          <span
            className="text-xs font-mono font-semibold px-3 py-1 rounded-full"
            style={{ background: 'rgba(249, 84, 0, 0.2)', color: '#f95400' }}
          >
            Coming Soon
          </span>
        </div>

        {/* Description */}
        <p className="text-xs text-gray-500 text-center mb-5">
          Exclusive reward campaigns from top projects. Register your interest to get notified when we launch.
        </p>

        {/* CTA */}
        {checkingStatus ? (
          <div className="flex justify-center py-3">
            <div className="w-6 h-6 border-2 border-[#f95400]/30 border-t-[#f95400] rounded-full animate-spin" />
          </div>
        ) : registered ? (
          <div
            className="h-12 rounded-2xl flex items-center justify-center gap-2"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
              border: '1px solid rgba(249, 84, 0, 0.4)',
            }}
          >
            <CheckIconFill className="w-5 h-5 text-white" />
            <span className="text-white font-semibold text-sm">You&apos;re on the list!</span>
          </div>
        ) : (
          <button
            onClick={() => {
              hapticFeedback('medium');
              setShowInterestModal(true);
            }}
            className="w-full h-12 rounded-2xl text-sm font-semibold flex items-center justify-center gap-2 transition-all active:scale-95"
            style={{
              background: 'linear-gradient(135deg, rgba(249, 84, 0, 0.2) 0%, rgba(255, 140, 66, 0.15) 50%, rgba(249, 84, 0, 0.18) 100%)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(249, 84, 0, 0.4)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5), 0 1px 0 rgba(255, 140, 66, 0.2) inset',
              color: 'white',
            }}
          >
            Register Interest
          </button>
        )}
      </div>

      {/* Interest Modal */}
      <CampaignInterestModal
        isOpen={showInterestModal}
        onClose={() => setShowInterestModal(false)}
        onSuccess={() => {
          setRegistered(true);
          setShowInterestModal(false);
          hapticFeedback('success');
        }}
      />
    </div>
  );
}
