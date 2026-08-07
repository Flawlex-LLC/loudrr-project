import { ArrowDown, ArrowUp } from "@phosphor-icons/react/dist/ssr";
import type { Delta } from "@/lib/mock";

// Finance-grade dual delta: absolute point swing + relative % side by side. Color never carries
// meaning alone — there's always an arrow. Server-safe.
export function DeltaPair({ delta, size = "md" }: { delta: Delta; size?: "sm" | "md" }) {
  const up = delta.abs >= 0;
  const col = up ? "text-up" : "text-down";
  const Icon = up ? ArrowUp : ArrowDown;
  const text = size === "sm" ? "text-xs" : "text-sm";
  return (
    <span className={`inline-flex items-center gap-1.5 font-mono tabular-nums ${text} ${col}`}>
      <Icon size={size === "sm" ? 11 : 13} weight="bold" />
      <span className="font-semibold">
        {up ? "+" : "−"}
        {Math.abs(Math.round(delta.abs)).toLocaleString()}
      </span>
      <span className="text-bone-500">
        ({up ? "+" : "−"}
        {Math.abs(delta.relPct).toFixed(1)}%)
      </span>
    </span>
  );
}
