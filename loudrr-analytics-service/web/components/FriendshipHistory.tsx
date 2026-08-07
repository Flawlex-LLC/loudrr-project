"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { UserMinus, UserPlus } from "@phosphor-icons/react";
import { avatar, ROLE_COLOR, ROLE_LABEL, type FriendshipEvent } from "@/lib/mock";
import { tierFor } from "@/lib/score";

// Timeline of who started / stopped following — the "alpha" narrative (a Tier-1 fund quietly
// entering, a project exiting). Filterable by follow / unfollow.
export function FriendshipHistory({ events }: { events: FriendshipEvent[] }) {
  const [filter, setFilter] = useState<"all" | "follow" | "unfollow">("all");
  const rows = filter === "all" ? events : events.filter((e) => e.type === filter);
  const follows = events.filter((e) => e.type === "follow").length;
  const unfollows = events.length - follows;

  return (
    <div>
      <div className="mb-4 flex gap-1.5">
        <Pill active={filter === "all"} onClick={() => setFilter("all")}>
          All <span className="text-bone-500">{events.length}</span>
        </Pill>
        <Pill active={filter === "follow"} onClick={() => setFilter("follow")}>
          Follows <span className="text-up">{follows}</span>
        </Pill>
        <Pill active={filter === "unfollow"} onClick={() => setFilter("unfollow")}>
          Unfollows <span className="text-down">{unfollows}</span>
        </Pill>
      </div>

      <ol className="relative space-y-1 before:absolute before:left-[19px] before:top-2 before:bottom-2 before:w-px before:bg-white/8">
        {rows.map((e, i) => {
          const follow = e.type === "follow";
          const col = tierFor(e.score).color;
          return (
            <li key={`${e.handle}-${i}`} className="relative flex items-center gap-3 rounded-lg py-1.5 pl-1">
              <span
                className={`relative z-10 grid h-10 w-10 shrink-0 place-items-center rounded-full border ${
                  follow ? "border-up/30 bg-up/10 text-up" : "border-down/30 bg-down/10 text-down"
                }`}
              >
                {follow ? <UserPlus size={16} weight="fill" /> : <UserMinus size={16} weight="fill" />}
              </span>
              <Link href={`/score/${e.handle}`} className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg p-1.5 transition-colors hover:bg-white/[0.03]">
                <Image
                  src={avatar(e.handle)}
                  alt={e.handle}
                  width={28}
                  height={28}
                  unoptimized
                  className="h-7 w-7 shrink-0 rounded-full bg-ink-700 object-cover"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-bone-100">
                    <span className="font-medium">@{e.handle}</span>{" "}
                    <span className="text-bone-500">{follow ? "started following" : "unfollowed"}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: ROLE_COLOR[e.role] }} />
                    <span className="font-mono text-[11px] text-bone-500">{ROLE_LABEL[e.role]}</span>
                  </div>
                </div>
                <span className="font-mono text-sm font-semibold tabular-nums" style={{ color: col }}>
                  {e.score.toLocaleString()}
                </span>
                <span className="w-14 text-right font-mono text-[11px] text-bone-600">{e.daysAgo}d ago</span>
              </Link>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Pill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      data-active={active}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 font-mono text-xs transition-colors ${
        active ? "border-white/20 bg-white/[0.06] text-bone-100" : "border-white/[0.07] text-bone-500 hover:text-bone-200"
      }`}
    >
      {children}
    </button>
  );
}
