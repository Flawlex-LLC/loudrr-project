'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * One async read with honest, separable states.
 *
 * WHY: three admin pages modelled their fetch as `rows: T[] | null` and used
 * `rows === null` to mean BOTH "still loading" and "the request blew up" —
 * then disabled the Refresh button on that same null. A failed first load
 * therefore parked the page on a spinner with its only escape hatch greyed
 * out; the admin's only recourse was a browser reload.
 *
 * Here `loading` and `error` are independent, `data` stays null only when
 * there is genuinely nothing to show, and `reload` is always callable.
 */
export interface AdminResource<T> {
  /** Last successful payload; null until the first load succeeds. */
  data: T | null;
  /** True while a fetch is in flight — including a `reload()` over stale data. */
  loading: boolean;
  /** Message from the last failed fetch, cleared by the next success. */
  error: string | null;
  /** Re-run the fetcher. Safe to wire straight to a Refresh button. */
  reload: () => Promise<void>;
  /** Patch the cached data locally (optimistic row updates) without a round-trip. */
  mutate: (next: T | null | ((prev: T | null) => T | null)) => void;
}

/**
 * @param fetcher Async read. Re-created every render is fine — it is held in a
 *                ref, so only `deps` decide when a refetch happens.
 * @param deps    Re-fetch when these change (same contract as useEffect deps).
 *
 * @example
 *   const { data: rows, loading, error, reload, mutate } =
 *     useAdminResource(() => adminApi.searchUsers(q), [q]);
 */
export function useAdminResource<T>(
  fetcher: () => Promise<T>,
  deps: React.DependencyList = [],
): AdminResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Keep the newest fetcher without making it a dependency: pages pass inline
  // closures, which would otherwise refetch on every single render.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  // Monotonic request id — a slow response from an abandoned query (old search
  // term, unmounted page) must never overwrite a newer one.
  const seqRef = useRef(0);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const run = useCallback(async () => {
    const seq = ++seqRef.current;
    setLoading(true);
    try {
      const result = await fetcherRef.current();
      if (seq !== seqRef.current || !mountedRef.current) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (seq !== seqRef.current || !mountedRef.current) return;
      setError(e instanceof Error ? e.message : 'Request failed');
      // Deliberately keep the previous `data`: a failed refresh should leave
      // the last-known table on screen with an error banner above it, not
      // blank the page.
    } finally {
      if (seq === seqRef.current && mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void run();
    // `deps` is the caller's dependency list — spreading it is the whole point
    // of the hook, and the linter can't statically verify a dynamic list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, ...deps]);

  const mutate = useCallback((next: T | null | ((prev: T | null) => T | null)) => {
    setData((prev) => (typeof next === 'function' ? (next as (p: T | null) => T | null)(prev) : next));
  }, []);

  return { data, loading, error, reload: run, mutate };
}
