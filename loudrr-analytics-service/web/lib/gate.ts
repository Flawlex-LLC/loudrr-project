"use client";

// Free-tier gate: anonymous visitors may view N profiles, then we ask them to sign in
// (same pattern as twitterscore / sorsa). Tracked client-side in localStorage; the real
// enforcement moves server-side with auth later.

export const FREE_VIEW_LIMIT = 3;
// TESTING: unlimited lookups — flip to false to re-enable the free-tier gate.
const GATE_DISABLED = true;
const KEY = "loudrr.viewed.v1";

function read(): string[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export function viewedCount(): number {
  return read().length;
}

export function remainingFreeViews(): number {
  if (GATE_DISABLED) return FREE_VIEW_LIMIT;
  return Math.max(0, FREE_VIEW_LIMIT - read().length);
}

// records a view; returns true if this view is still within the free allowance
export function registerView(handle: string): boolean {
  if (GATE_DISABLED) return true;
  const seen = read();
  const key = handle.replace(/^@/, "").toLowerCase();
  if (seen.includes(key)) return seen.length <= FREE_VIEW_LIMIT; // re-viewing is free
  if (seen.length >= FREE_VIEW_LIMIT) return false;
  seen.push(key);
  localStorage.setItem(KEY, JSON.stringify(seen));
  return true;
}

export function isGated(handle: string): boolean {
  if (GATE_DISABLED) return false;
  const seen = read();
  const key = handle.replace(/^@/, "").toLowerCase();
  if (seen.includes(key)) return false;
  return seen.length >= FREE_VIEW_LIMIT;
}
