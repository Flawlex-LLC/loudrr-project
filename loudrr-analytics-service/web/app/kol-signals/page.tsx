import type { Metadata } from "next";
import { KolSignals } from "@/components/KolSignals";

export const metadata: Metadata = {
  title: "KOL Signals — Loudrr",
  description:
    "Off-chain social sentiment: the hottest tokens called by tracked smart accounts, joined with live market data.",
};

export default function KolSignalsPage() {
  return <KolSignals />;
}
