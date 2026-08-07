import { redirect } from "next/navigation";

// Renamed 2026-07-02: the section is "KOL Signals" (calls = the tweets; rides = wallet buys).
export default function LegacyKolCalls() {
  redirect("/kol-signals");
}
