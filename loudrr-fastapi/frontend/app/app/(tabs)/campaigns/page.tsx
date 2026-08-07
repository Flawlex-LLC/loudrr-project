'use client';

/** /app/campaigns — Campaigns tab page. Shared state comes from the (tabs) layout. */
import { CampaignsTab } from '../../screens/campaigns-tab';
import { useTabContext } from '../tab-context';

export default function CampaignsTabPage() {
  const { user } = useTabContext();
  return <CampaignsTab user={user} />;
}
