"use client";

import { motion } from "framer-motion";
import { ArrowRight, MagnifyingGlass } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { searchAccounts, USE_LIVE } from "@/lib/api";
import { LEADERBOARD } from "@/lib/mock";

type Suggestion = { handle: string; name: string; score: number };

export function SearchBar({ size = "lg", autoFocus = false }: { size?: "lg" | "sm"; autoFocus?: boolean }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [focus, setFocus] = useState(false);
  const [liveMatches, setLiveMatches] = useState<Suggestion[]>([]);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const go = (handle?: string) => {
    const h = (handle ?? q).trim().replace(/^@/, "");
    if (!h) return;
    router.push(`/score/${encodeURIComponent(h)}`);
  };

  // live typeahead over the REAL ranked universe (debounced)
  useEffect(() => {
    if (!USE_LIVE) return;
    if (debounce.current) clearTimeout(debounce.current);
    if (q.trim().length < 2) {
      setLiveMatches([]);
      return;
    }
    debounce.current = setTimeout(() => {
      searchAccounts(q).then((items) =>
        setLiveMatches(items.slice(0, 5).map((i) => ({
          handle: i.userName, name: i.name || i.userName, score: i.score,
        }))));
    }, 180);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [q]);

  const matches: Suggestion[] = USE_LIVE
    ? liveMatches
    : q.length > 0
      ? LEADERBOARD.filter(
          (p) => p.handle.toLowerCase().includes(q.toLowerCase()) || p.name.toLowerCase().includes(q.toLowerCase()),
        ).slice(0, 5).map((p) => ({ handle: p.handle, name: p.name, score: p.score }))
      : [];

  const big = size === "lg";

  return (
    <div className="relative w-full">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          go();
        }}
        className={`group relative flex items-center gap-3 rounded-2xl border bg-ink-800/70 backdrop-blur transition-all ${
          focus ? "border-flare/60" : "border-white/10"
        } ${big ? "px-5 py-4" : "px-4 py-2.5"}`}
      >
        <span className={`text-bone-500 transition-colors ${focus ? "!text-flare" : ""}`}>
          <MagnifyingGlass size={big ? 22 : 18} weight="bold" />
        </span>
        <span className={`select-none font-mono text-bone-600 ${big ? "text-lg" : "text-sm"}`}>@</span>
        <input
          value={q}
          autoFocus={autoFocus}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => setFocus(true)}
          onBlur={() => setTimeout(() => setFocus(false), 140)}
          placeholder="enter an X handle"
          spellCheck={false}
          className={`min-w-0 flex-1 bg-transparent font-mono text-bone-100 placeholder:text-bone-600 focus:outline-none ${
            big ? "text-lg" : "text-sm"
          }`}
        />
        <button
          type="submit"
          className={`btn-flare shrink-0 ${big ? "" : "!px-3 !py-1.5 !text-sm"}`}
          aria-label="Get score"
        >
          {big ? "Get score" : ""} <ArrowRight size={big ? 17 : 15} />
        </button>

        {/* autocomplete */}
        {focus && matches.length > 0 && (
          <motion.ul
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            className="panel absolute left-0 right-0 top-[calc(100%+8px)] z-30 overflow-hidden p-1.5"
          >
            {matches.map((p) => (
              <li key={p.handle}>
                <button
                  type="button"
                  onMouseDown={() => go(p.handle)}
                  className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left transition-colors hover:bg-white/5"
                >
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-sm text-bone-200">@{p.handle}</span>
                    <span className="text-xs text-bone-500">{p.name}</span>
                  </span>
                  <span className="font-mono text-sm tabular-nums text-flare">{p.score.toLocaleString()}</span>
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </form>

    </div>
  );
}
