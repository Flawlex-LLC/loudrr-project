"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getLeaderboard, USE_LIVE, type LeaderboardItem } from "@/lib/api";
import { avatar, LEADERBOARD } from "@/lib/mock";
import { fmtCompact } from "@/lib/score";

type Row = {
  rank: number; handle: string; name: string; score: number;
  eliteFollowers: number | null; followers: number | null;
};

// Home leaderboard teaser — top 8 of the REAL ranked universe when live.
export function HomeTeaser() {
  const [rows, setRows] = useState<Row[] | null>(USE_LIVE ? null : mockRows());

  useEffect(() => {
    if (!USE_LIVE) return;
    let dead = false;
    getLeaderboard(8).then((r) => {
      if (!dead) setRows((r?.items ?? []).map(toRow));
    });
    return () => {
      dead = true;
    };
  }, []);

  if (rows === null) {
    return (
      <div className="panel divide-y divide-white/[0.07] overflow-hidden">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 px-4 py-3">
            <div className="skel h-4 w-5" />
            <div className="skel h-9 w-9 rounded-full" />
            <div className="skel h-4 w-40" />
            <div className="skel ml-auto h-4 w-16" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="panel divide-y divide-white/[0.07] overflow-hidden">
      {rows.map((p) => (
        <Link
          key={p.handle}
          href={`/score/${p.handle}`}
          className="grid grid-cols-[28px_1fr_auto] items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.03] sm:grid-cols-[28px_1fr_110px_110px_88px]"
        >
          <span className="font-mono text-sm tabular-nums text-bone-500">{p.rank}</span>
          <span className="flex min-w-0 items-center gap-3">
            <Image
              src={avatar(p.handle)}
              alt={p.handle}
              width={36}
              height={36}
              unoptimized
              className="h-9 w-9 rounded-full bg-ink-700 object-cover"
            />
            <span className="min-w-0">
              <span className="block truncate font-medium text-bone-100">{p.name}</span>
              <span className="block truncate font-mono text-xs text-bone-500">@{p.handle}</span>
            </span>
          </span>
          <span className="hidden text-right font-mono text-sm tabular-nums text-bone-300 sm:block">
            {p.eliteFollowers != null ? `${fmtCompact(p.eliteFollowers)} smart` : "—"}
          </span>
          <span className="hidden text-right font-mono text-sm tabular-nums text-bone-500 sm:block">
            {p.followers != null ? fmtCompact(p.followers) : "—"}
          </span>
          <span className="text-right font-mono text-lg font-bold tabular-nums text-bone-100">
            {p.score.toLocaleString()}
          </span>
        </Link>
      ))}
    </div>
  );
}

function toRow(i: LeaderboardItem): Row {
  return {
    rank: i.rank, handle: i.userName, name: i.name || i.userName, score: i.score,
    eliteFollowers: i.eliteFollowers, followers: i.followers,
  };
}

function mockRows(): Row[] {
  return LEADERBOARD.slice(0, 8).map((p) => ({
    rank: p.rank, handle: p.handle, name: p.name, score: p.score,
    eliteFollowers: p.eliteFollowers, followers: p.followers,
  }));
}
