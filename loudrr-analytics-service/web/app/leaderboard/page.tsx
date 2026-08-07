import type { Metadata } from "next";
import { Leaderboard } from "@/components/Leaderboard";
import { SearchBar } from "@/components/SearchBar";

export const metadata: Metadata = {
  title: "Loudrr Rank",
  description: "The loudest accounts on crypto X, ranked by Loudrr Score — the influence score for crypto.",
};

export default function LeaderboardPage() {
  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <p className="eyebrow">The rankings</p>
        <h1 className="mt-2 font-display text-4xl font-extrabold tracking-tightest sm:text-5xl">Loudrr Rank</h1>
        <p className="mt-3 max-w-xl text-bone-400">
          Every account on crypto X, ranked by <span className="text-bone-200">Loudrr Score</span> — weighed by the
          quality of who follows them. Updated continuously.
        </p>
      </header>

      <div className="mb-7 max-w-md">
        <SearchBar size="sm" />
      </div>

      <Leaderboard />
    </div>
  );
}
