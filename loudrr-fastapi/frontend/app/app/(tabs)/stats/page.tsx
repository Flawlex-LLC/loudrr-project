'use client';

/**
 * /app/stats — Stats route.
 *
 * Renders the StatsModal as a real, deep-linkable route. It still looks like
 * a slide-up modal overlay; the browser back button (or the modal's close
 * button) navigates back to the tab the user came from.
 */
import { useRouter } from 'next/navigation';
import { StatsModal } from '../../modals/stats';

export default function StatsRoute() {
  const router = useRouter();
  return <StatsModal isOpen onClose={() => router.back()} />;
}
