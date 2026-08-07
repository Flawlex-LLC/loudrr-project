import { ScoreView } from "./ScoreView";

export async function generateMetadata({ params }: { params: Promise<{ handle: string }> }) {
  const { handle } = await params;
  const h = decodeURIComponent(handle).replace(/^@/, "");
  return {
    title: `@${h}'s Loudrr Score`,
    description: `See @${h}'s Loudrr Score — the influence score for crypto X.`,
  };
}

export default async function ScorePage({ params }: { params: Promise<{ handle: string }> }) {
  const { handle: raw } = await params;
  const handle = decodeURIComponent(raw).replace(/^@/, "");
  return <ScoreView handle={handle} />;
}
