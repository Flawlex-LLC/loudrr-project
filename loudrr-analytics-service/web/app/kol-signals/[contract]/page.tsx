import type { Metadata } from "next";
import { TokenSignals } from "@/components/TokenSignals";

export const metadata: Metadata = {
  title: "Token signals — Loudrr",
  description: "Price action with every tracked KOL call pinned at its time and price.",
};

export default async function TokenSignalsPage({
  params,
}: {
  params: Promise<{ contract: string }>;
}) {
  const { contract } = await params;
  return <TokenSignals contract={decodeURIComponent(contract)} />;
}
