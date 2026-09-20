'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { AlertTriangle, RefreshCw } from 'lucide-react';

import { Button } from '@/components/admin/Button';

/**
 * Segment error boundary for the admin panel.
 *
 * Without this, a single render throw on any admin page (a null field a page
 * assumed was present, a bad date, a mapped array that came back as an object)
 * unmounted the whole tree and left admins staring at a black screen with no
 * hint and no way back short of a browser reload.
 *
 * This renders INSIDE the shell's <main>, so the sidebar, breadcrumb and
 * navigation all keep working while one page is broken.
 */
export default function AdminError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Keep the stack in the console — the digest alone is useless locally.
    console.error('[admin] page crashed:', error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-4">
      <div className="w-full max-w-lg rounded-2xl border border-white/[0.08] bg-[#111] p-6 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full border border-red-900/50 bg-red-950/40">
          <AlertTriangle size={22} className="text-red-400" />
        </div>

        <h2 className="mt-4 font-syne text-lg font-bold tracking-tight text-white">
          This page hit an error
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-zinc-400">
          The rest of the panel still works — use the navigation, or try again. Nothing was
          submitted.
        </p>

        {(error.message || error.digest) && (
          <pre className="mt-4 max-h-40 overflow-auto rounded-lg border border-white/[0.06] bg-[#0a0a0a] p-3 text-left font-mono text-[11px] leading-relaxed text-zinc-400">
            {error.message || `digest: ${error.digest}`}
            {error.message && error.digest ? `\n\ndigest: ${error.digest}` : ''}
          </pre>
        )}

        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Button variant="primary" onClick={reset}>
            <RefreshCw size={14} /> Try again
          </Button>
          <Link href="/admin">
            <Button variant="secondary">Back to dashboard</Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
