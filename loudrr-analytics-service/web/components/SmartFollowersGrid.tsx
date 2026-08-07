"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { avatar, ROLE_COLOR, ROLE_LABEL, ROLES, type Receipt, type Role } from "@/lib/mock";
import { fmtCompact, tierFor } from "@/lib/score";

// Role-tabbed smart-followers grid — the depth upgrade to a flat receipts strip. Tabs show the
// per-role count (from the full breakdown); the list shows the notable sampled voters per role.
export function SmartFollowersGrid({
  receipts,
  byCategory,
}: {
  receipts: Receipt[];
  byCategory: Record<Role, number>;
}) {
  const present = ROLES.filter((r) => (byCategory[r] ?? 0) > 0);
  const total = present.reduce((a, r) => a + byCategory[r], 0);
  // live real profiles have no per-scene breakdown (yet) — render a flat receipts list
  const hasBreakdown = total > 0;
  const [tab, setTab] = useState<Role | "all">("all");

  const list = tab === "all" || !hasBreakdown ? receipts : receipts.filter((r) => r.role === tab);
  const shownCount = !hasBreakdown ? receipts.length : tab === "all" ? total : byCategory[tab];
  const moreInCategory = Math.max(0, shownCount - list.length);

  return (
    <div>
      {hasBreakdown && (
        <div className="no-scrollbar -mx-1 mb-4 flex gap-1.5 overflow-x-auto px-1 pb-1">
          <Tab active={tab === "all"} onClick={() => setTab("all")} label="All" count={total} color="#ff6a00" />
          {present.map((r) => (
            <Tab
              key={r}
              active={tab === r}
              onClick={() => setTab(r)}
              label={ROLE_LABEL[r]}
              count={byCategory[r]}
              color={ROLE_COLOR[r]}
            />
          ))}
        </div>
      )}

      {list.length === 0 ? (
        <p className="px-1 py-6 text-center font-mono text-xs text-bone-600">
          {hasBreakdown
            ? `${shownCount.toLocaleString()} ${tab === "all" ? "smart followers" : ROLE_LABEL[tab as Role].toLowerCase()} — notable names load on the full tier.`
            : "No notable-follower receipts tracked for this account yet."}
        </p>
      ) : (
        <div className="grid max-h-[360px] gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
          {list.map((r) => {
            const col = tierFor(r.score).color;
            return (
              <Link
                key={r.handle}
                href={`/score/${r.handle}`}
                className="group flex items-center gap-3 rounded-xl border border-white/[0.06] bg-ink-800/40 p-2.5 transition-colors hover:border-flare/30 hover:bg-ink-800/70"
              >
                <Image
                  src={avatar(r.handle)}
                  alt={r.handle}
                  width={36}
                  height={36}
                  unoptimized
                  className="h-9 w-9 shrink-0 rounded-full bg-ink-700 object-cover"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-bone-100">{r.name}</div>
                  {hasBreakdown ? (
                    <div className="flex items-center gap-1.5">
                      <span className="h-1.5 w-1.5 rounded-full" style={{ background: ROLE_COLOR[r.role] }} />
                      <span className="truncate font-mono text-[11px] text-bone-500">{ROLE_LABEL[r.role]}</span>
                    </div>
                  ) : (
                    <div className="truncate font-mono text-[11px] text-bone-500">@{r.handle}</div>
                  )}
                </div>
                <span className="font-mono text-sm font-semibold tabular-nums" style={{ color: col }}>
                  {r.score.toLocaleString()}
                </span>
              </Link>
            );
          })}
          {moreInCategory > 0 && (
            <div className="flex items-center justify-center rounded-xl border border-dashed border-white/[0.07] p-2.5 font-mono text-xs text-bone-600">
              + {fmtCompact(moreInCategory)} more
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Tab({
  active,
  onClick,
  label,
  count,
  color,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  color: string;
}) {
  return (
    <button
      onClick={onClick}
      data-active={active}
      className={`flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-xs transition-colors ${
        active ? "border-white/20 bg-white/[0.06] text-bone-100" : "border-white/[0.07] text-bone-500 hover:text-bone-200"
      }`}
    >
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      {label}
      <span className="tabular-nums text-bone-500">{fmtCompact(count)}</span>
    </button>
  );
}
