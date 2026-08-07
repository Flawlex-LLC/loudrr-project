"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import { ScoreDashboard } from "@/components/ScoreDashboard";
import { PixelLoader } from "@/components/PixelLoader";
import { getScore } from "@/lib/api";
import type { Profile } from "@/lib/mock";

export function ScoreView({ handle }: { handle: string }) {
  const [state, setState] = useState<Profile | "loading" | "notfound">("loading");

  useEffect(() => {
    let alive = true;
    setState("loading");
    getScore(handle).then((p) => {
      if (alive) setState(p ?? "notfound");
    });
    return () => {
      alive = false;
    };
  }, [handle]);

  if (state === "loading") return <Scanning handle={handle} />;
  if (state === "notfound") return <NotTracked handle={handle} />;
  return <ScoreDashboard profile={state} />;
}

// Honest empty state — we NEVER fabricate a profile for an account we haven't scored.
function NotTracked({ handle }: { handle: string }) {
  return (
    <div className="panel grid place-items-center gap-4 px-6 py-24 text-center">
      <MagnifyingGlass size={28} className="text-bone-600" />
      <div>
        <div className="font-display text-lg font-bold tracking-tight">
          @{handle} isn&apos;t scored yet
        </div>
        <p className="mx-auto mt-2 max-w-sm text-sm text-bone-500">
          We rank accounts by the quality of who follows them. This one hasn&apos;t entered the
          ranked universe yet — check back as coverage grows.
        </p>
      </div>
      <Link href="/leaderboard" className="btn-ghost">Browse Loudrr Rank</Link>
    </div>
  );
}

function Scanning({ handle }: { handle: string }) {
  return (
    <div className="panel grid place-items-center gap-5 px-6 py-24 text-center">
      <PixelLoader />
      <div>
        <div className="font-display text-lg font-bold tracking-tight">Measuring the signal</div>
        <div className="mt-1 font-mono text-sm text-bone-500">
          scoring <span className="text-flare">@{handle}</span>…
        </div>
      </div>
      <div className="h-1 w-48 overflow-hidden rounded-full bg-white/5">
        <div className="shimmer h-full w-full" />
      </div>
    </div>
  );
}
