import { LockSimple, Sparkle } from "@phosphor-icons/react/dist/ssr";
import type { Badge } from "@/lib/score";

// Achievement rail — earned badges glow, locked ones are dimmed with a lock. Server-safe.
export function BadgeRail({ badges }: { badges: Badge[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {badges.map((b) => (
        <span
          key={b.key}
          title={b.hint}
          className={`group inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 font-mono text-xs transition-colors ${
            b.earned
              ? "border-flare/40 bg-flare/10 text-flare"
              : "border-white/[0.07] bg-white/[0.02] text-bone-600"
          }`}
        >
          {b.key === "tier" ? (
            <span aria-hidden>◢</span>
          ) : b.earned ? (
            <Sparkle size={12} weight="fill" />
          ) : (
            <LockSimple size={12} weight="fill" />
          )}
          {b.label}
        </span>
      ))}
    </div>
  );
}
