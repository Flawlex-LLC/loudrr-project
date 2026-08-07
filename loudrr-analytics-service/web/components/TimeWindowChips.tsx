"use client";

import { WINDOWS, type WindowKey } from "@/lib/mock";

// Segmented time-window control (24H / 7D / 30D / 3M / 1Y). Controlled.
export function TimeWindowChips({ value, onChange }: { value: WindowKey; onChange: (w: WindowKey) => void }) {
  return (
    <div className="seg" role="tablist" aria-label="Time window">
      {WINDOWS.map((w) => (
        <button
          key={w.key}
          role="tab"
          aria-selected={value === w.key}
          data-active={value === w.key}
          onClick={() => onChange(w.key)}
          className="seg-item"
        >
          {w.label}
        </button>
      ))}
    </div>
  );
}
