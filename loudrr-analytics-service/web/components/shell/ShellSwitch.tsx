"use client";

import { AppShell } from "./AppShell";

// The sidebar app-shell wraps EVERY route, including `/` (the marketing/selling home) — the sidebar
// is always present; its "Home" item is the marketing page. (Kept as a thin wrapper so layout.tsx's
// import stays stable; no more per-route marketing chrome.)
export function ShellSwitch({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
