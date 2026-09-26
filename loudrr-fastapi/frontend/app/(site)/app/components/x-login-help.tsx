'use client';

import { useState } from 'react';
import { hapticFeedback } from '@/lib/telegram';

/**
 * Shown while we wait for X sign-in. If X errors on the phone ("Something went
 * wrong", "flow name login is currently inaccessible"), the sign-in link pasted
 * into Chrome or Safari always works; the app picks the result up from there.
 */
export function XLoginHelp({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      hapticFeedback('success');
      setTimeout(() => setCopied(false), 2500);
    } catch {
      hapticFeedback('error');
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] p-3 text-left">
      <p className="text-xs font-medium text-gray-200">X showing &ldquo;Something went wrong&rdquo;?</p>
      <p className="mt-1 text-[11px] leading-snug text-gray-500">
        Copy the sign-in link, paste it into Chrome or Safari and approve Loudrr there. Then come back here.
      </p>
      <button
        type="button"
        onClick={copy}
        className="mt-2 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-white active:scale-95"
      >
        {copied ? 'Link copied' : 'Copy sign-in link'}
      </button>
    </div>
  );
}
