"use client";

import { IconContext } from "@phosphor-icons/react";
import { AuthGateProvider } from "./AuthGate";

// Phosphor icons default to fill weight app-wide; auth gate wraps everything so any
// component can call requireLogin() on a gated action.
export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <IconContext.Provider value={{ weight: "fill", mirrored: false }}>
      <AuthGateProvider>{children}</AuthGateProvider>
    </IconContext.Provider>
  );
}
