'use client';

/** /app/engage — Engage tab page. Shared state comes from the (tabs) layout. */
import { EngageTab } from '../../screens/engage-tab';
import { useTabContext } from '../tab-context';

export default function EngageTabPage() {
  const { user, loadUser, engageData, setEngageData, settings, activeTab } = useTabContext();
  return (
    <EngageTab
      user={user}
      onUserUpdate={loadUser}
      engageData={engageData}
      setEngageData={setEngageData}
      settings={settings}
      activeTab={activeTab}
    />
  );
}
