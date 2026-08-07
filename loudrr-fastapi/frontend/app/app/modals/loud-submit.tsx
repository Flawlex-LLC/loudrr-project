'use client';

import { useState } from 'react';
import { hapticFeedback } from '@/lib/telegram';
import { loudApi, LoudProject, LoudProjectsResponse, LoudSubmitResponse, normalizeXLink } from '@/lib/api';
import { ICON_GRADIENT_STYLE } from '../shared';
import { FireIconFill } from '../icons';

/**
 * Loudrr Mini App — LoudSubmitModal
 * Extracted from app/app/page.tsx during the modularization refactor.
 */

export function LoudSubmitModal({
  projects,
  projectsData,
  preselectedProject,
  onClose,
  onSuccess,
}: {
  projects: LoudProject[];
  projectsData: LoudProjectsResponse;
  preselectedProject?: LoudProject | null;
  onClose: () => void;
  onSuccess: (result: LoudSubmitResponse) => void;
}) {
  const [selectedProject, setSelectedProject] = useState<LoudProject | null>(preselectedProject || null);
  const [xLink, setXLink] = useState('');
  const [urlValidation, setUrlValidation] = useState<{
    valid: boolean;
    normalized: string;
    error?: string;
  }>({ valid: false, normalized: '' });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState<LoudSubmitResponse | null>(null);

  const eligibleProjects = projects.filter(p => p.can_submit);

  const handleUrlChange = (value: string) => {
    setXLink(value);
    setSubmitError(null);

    if (!value.trim()) {
      setUrlValidation({ valid: false, normalized: '' });
      return;
    }

    const result = normalizeXLink(value);
    setUrlValidation({
      valid: result.valid,
      normalized: result.normalized,
      error: result.error,
    });
  };

  const handleSubmit = async () => {
    if (!urlValidation.valid || !selectedProject) return;

    try {
      setSubmitting(true);
      setSubmitError(null);
      const result = await loudApi.submit(selectedProject.id, xLink);

      if (result.success) {
        setSubmitSuccess(result);
        hapticFeedback('success');
        setTimeout(() => {
          onSuccess(result);
        }, 2500);
      } else {
        setSubmitError(result.error || 'Submission failed');
        hapticFeedback('error');
      }
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Submission failed');
      hapticFeedback('error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/90 backdrop-blur-md" onClick={onClose} />

      {/* Modal */}
      <div className="relative w-full max-w-lg bg-black/95 backdrop-blur-xl border-t border-x border-[#f95400]/20 rounded-t-3xl max-h-[90vh] flex flex-col animate-slide-up">
        {/* Handle */}
        <div className="flex justify-center pt-3 pb-2">
          <div className="w-10 h-1 rounded-full bg-[#f95400]/40" />
        </div>

        {/* Header */}
        <div className="px-5 pb-4 border-b border-white/10">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold text-white">Submit Content</h2>
            <button
              onClick={onClose}
              className="p-2 rounded-full hover:bg-white/10 transition-colors"
            >
              <svg className="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {submitSuccess ? (
            /* Success State */
            <div className="text-center py-8">
              <div className="w-20 h-20 mx-auto mb-4 rounded-full gold-gradient-bg flex items-center justify-center shadow-lg shadow-[#f95400]/30">
                <svg className="w-10 h-10 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h3 className="text-xl font-bold text-white mb-2">Submitted!</h3>
              <p className="text-4xl font-bold gold-gradient-text mb-1">+{submitSuccess.points_awarded}</p>
              <p className="text-gray-400">points earned</p>
              <p className="text-sm text-gray-500 mt-3">
                Now ranked <span className="text-[#f95400] font-semibold">#{submitSuccess.new_rank}</span> in {selectedProject?.name}
              </p>
            </div>
          ) : (
            <>
              {/* Step 1: Select Project */}
              <div>
                <label className="text-sm font-medium text-gray-400 mb-3 block">
                  Select Project
                </label>
                {eligibleProjects.length === 0 ? (
                  <div className="text-center py-6 bg-white/5 rounded-xl">
                    <p className="text-gray-400 text-sm">No projects available for submission</p>
                    <p className="text-gray-500 text-xs mt-1">You may have reached your limits</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {eligibleProjects.map((project) => (
                      <button
                        key={project.id}
                        onClick={() => setSelectedProject(project)}
                        className={`w-full p-3 rounded-xl border text-left transition-all flex items-center gap-3 ${
                          selectedProject?.id === project.id
                            ? 'border-[#f95400] bg-[#f95400]/10'
                            : 'border-white/10 bg-white/5 hover:border-white/20 hover:bg-white/[0.07]'
                        }`}
                      >
                        <div className={`w-11 h-11 rounded-xl flex items-center justify-center overflow-hidden ${
                          selectedProject?.id === project.id ? 'ring-2 ring-[#f95400]/50' : 'bg-white/10'
                        }`}>
                          {project.logo_url ? (
                            <img src={project.logo_url} alt="" className="w-full h-full object-cover" />
                          ) : (
                            <span className="font-bold text-white">{project.name.charAt(0)}</span>
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className={`font-medium ${selectedProject?.id === project.id ? 'text-white' : 'text-gray-200'}`}>
                            {project.name}
                          </p>
                          <p className="text-xs text-gray-500">
                            {project.max_submissions - project.user_submissions} submissions left
                          </p>
                        </div>
                        {selectedProject?.id === project.id && (
                          <div className="w-6 h-6 rounded-full gold-gradient-bg flex items-center justify-center">
                            <svg className="w-4 h-4 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                          </div>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Step 2: URL Input (only shown when project selected) */}
              {selectedProject && (
                <>
                  <div>
                    <label className="text-sm font-medium text-gray-400 mb-2 block">
                      X Post Link
                    </label>
                    <input
                      type="url"
                      value={xLink}
                      onChange={(e) => handleUrlChange(e.target.value)}
                      placeholder="https://x.com/username/status/..."
                      className="w-full px-4 py-3.5 bg-white/5 border border-white/10 rounded-xl text-white placeholder-gray-500 focus:border-[#f95400]/50 focus:outline-none transition-colors"
                    />
                    {urlValidation.error && (
                      <p className="mt-2 text-sm text-red-400">{urlValidation.error}</p>
                    )}
                    {urlValidation.valid && (
                      <p className="mt-2 text-xs text-green-400 flex items-center gap-1">
                        <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                        </svg>
                        Valid link
                      </p>
                    )}
                  </div>

                  {/* Expected Points Display */}
                  <div className="bg-[#f95400]/10 border border-[#f95400]/30 rounded-xl p-4 text-center">
                    <p className="text-sm text-gray-400 mb-1">You'll earn</p>
                    <p className="text-3xl font-bold gold-gradient-text">{projectsData.expected_points}</p>
                    <p className="text-sm text-gray-500">points</p>
                  </div>

                  {/* Error Message */}
                  {submitError && (
                    <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4">
                      <p className="text-red-400 text-sm">{submitError}</p>
                    </div>
                  )}

                  {/* Submit Button */}
                  <button
                    onClick={handleSubmit}
                    disabled={!urlValidation.valid || submitting}
                    className="w-full py-4 btn-primary text-lg flex items-center justify-center gap-2"
                  >
                    {submitting ? (
                      <>
                        <span className="w-5 h-5 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                        Submitting...
                      </>
                    ) : (
                      <>
                        <FireIconFill className="w-5 h-5" style={ICON_GRADIENT_STYLE} />
                        Submit & Earn
                      </>
                    )}
                  </button>

                  {/* Limits Info */}
                  <div className="flex justify-between text-xs text-gray-500 pt-2">
                    <span>Daily: {projectsData.daily_submissions_remaining}/{projectsData.daily_limit}</span>
                    <span>{selectedProject.name}: {selectedProject.max_submissions - selectedProject.user_submissions} left</span>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
