import type { Metadata } from "next";
import { DeveloperConsole } from "@/components/DeveloperConsole";

export const metadata: Metadata = {
  title: "API",
  description:
    "The Loudrr API — a free, keyless endpoint that returns any X account's influence score. Free API keys for enriched data, custom tiers for scale.",
};

export default function DevelopersPage() {
  return (
    <div id="top" className="mx-auto max-w-5xl">
      <header className="mb-10 max-w-2xl">
        <p className="eyebrow">Developers</p>
        <h1 className="mt-2 font-display text-4xl font-extrabold tracking-tightest sm:text-5xl">The Loudrr API</h1>
        <p className="mt-4 text-lg leading-relaxed text-bone-400">
          One call returns any X account&apos;s influence score. The public endpoint is{" "}
          <span className="text-bone-100">free and keyless</span> — everything on this page is browsable without an
          account. You only sign in to mint your key.
        </p>
      </header>
      <DeveloperConsole />
    </div>
  );
}
