import { ROLE_COLOR, ROLE_LABEL, ROLES, type Role } from "@/lib/mock";
import { fmtCompact } from "@/lib/score";

// Horizontal stacked bar splitting the elite voter base by role + a legend with counts.
// Pure markup, server-safe.
export function AudienceBreakdown({ byCategory }: { byCategory: Record<Role, number> }) {
  const total = ROLES.reduce((a, r) => a + (byCategory[r] ?? 0), 0) || 1;
  const ordered = [...ROLES].sort((a, b) => (byCategory[b] ?? 0) - (byCategory[a] ?? 0));

  return (
    <div>
      <div className="flex h-3.5 w-full overflow-hidden rounded-full bg-white/5">
        {ordered.map((r) => {
          const w = ((byCategory[r] ?? 0) / total) * 100;
          if (w <= 0) return null;
          return (
            <div
              key={r}
              className="h-full transition-all"
              style={{ width: `${w}%`, background: ROLE_COLOR[r] }}
              title={`${ROLE_LABEL[r]} · ${(byCategory[r] ?? 0).toLocaleString()}`}
            />
          );
        })}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
        {ordered.map((r) => (
          <div key={r} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: ROLE_COLOR[r] }} />
            <span className="flex-1 truncate text-xs text-bone-400">{ROLE_LABEL[r]}</span>
            <span className="font-mono text-xs tabular-nums text-bone-200">{fmtCompact((byCategory[r] ?? 0))}</span>
            <span className="w-9 text-right font-mono text-[10px] tabular-nums text-bone-600">
              {Math.round(((byCategory[r] ?? 0) / total) * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
