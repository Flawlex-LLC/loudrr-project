'use client';

/** /app/home — Home tab page. Shared state comes from the (tabs) layout. */
import { HomeTab } from '../../screens/home-tab';
import { useTabContext } from '../tab-context';

export default function HomeTabPage() {
  const { user, loadUser, settings } = useTabContext();
  return <HomeTab user={user} onRefresh={loadUser} tiers={settings?.tiers} />;
}
