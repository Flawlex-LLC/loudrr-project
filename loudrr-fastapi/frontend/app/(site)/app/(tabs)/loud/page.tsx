'use client';

/** /app/loud — Loud tab page. Shared state comes from the (tabs) layout. */
import { LoudTab } from '../../screens/loud-tab';
import { useTabContext } from '../tab-context';

export default function LoudTabPage() {
  const { user } = useTabContext();
  return <LoudTab user={user} />;
}
