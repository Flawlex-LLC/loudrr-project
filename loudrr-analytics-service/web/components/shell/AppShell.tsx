"use client";

import { useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

// App-shell with an OpenSea-style sidebar: a slim ICON RAIL by default that expands on hover to
// reveal labels — overlaying the content (no layout shift). The top-bar toggle PINS it open (then
// it does push content). Mobile is an off-canvas drawer.
export function AppShell({ children }: { children: React.ReactNode }) {
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const expanded = pinned || hovered;

  return (
    <div className="min-h-screen">
      {/* desktop sidebar — icon rail; expands on hover (overlay) unless pinned */}
      <aside
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className={`fixed inset-y-0 left-0 z-40 hidden border-r border-white/[0.07] transition-[width] duration-200 lg:block ${
          expanded && !pinned ? "shadow-2xl shadow-black/60" : ""
        }`}
        style={{ width: expanded ? 248 : 64 }}
      >
        <Sidebar collapsed={!expanded} />
      </aside>

      {/* mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-ink-900/70 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-[248px] border-r border-white/[0.07]">
            <Sidebar collapsed={false} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      {/* content — offset by the rail (64) unless pinned (248); hover expands as an overlay */}
      <div className={`min-h-screen transition-[padding] duration-200 ${pinned ? "lg:pl-[248px]" : "lg:pl-[64px]"}`}>
        <TopBar onToggleCollapse={() => setPinned((v) => !v)} onOpenMobile={() => setMobileOpen(true)} />
        <main className="mx-auto w-full max-w-[1280px] px-4 py-6 sm:px-6 sm:py-8">{children}</main>
      </div>
    </div>
  );
}
