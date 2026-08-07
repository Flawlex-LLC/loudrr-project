'use client';

import { Inbox, type LucideIcon } from 'lucide-react';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon: Icon = Inbox, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-white/[0.06] bg-[#0d0d0d] px-6 py-16 text-center">
      <div className="rounded-full bg-white/[0.04] p-3">
        <Icon size={20} className="text-zinc-500" />
      </div>
      <h3 className="mt-4 text-sm font-medium text-white">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-sm text-zinc-500">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
