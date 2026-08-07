'use client';

import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: 'sm' | 'md' | 'lg';
}

export function Modal({ open, onClose, title, description, children, footer, size = 'md' }: ModalProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    // focus first focusable child for accessibility
    const t = setTimeout(() => {
      ref.current?.querySelector<HTMLElement>('input, textarea, button')?.focus();
    }, 50);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      clearTimeout(t);
      document.body.style.overflow = '';
    };
  }, [open, onClose]);

  if (!open) return null;

  const sizeClass = size === 'sm' ? 'max-w-sm' : size === 'lg' ? 'max-w-2xl' : 'max-w-md';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4 animate-in fade-in duration-150"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={ref}
        className={cn(
          'w-full rounded-2xl border border-white/[0.08] bg-[#111] shadow-2xl shadow-black/40',
          sizeClass
        )}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <div className="flex items-start justify-between border-b border-white/[0.06] px-6 py-4">
          <div>
            <h2 id="modal-title" className="text-base font-semibold text-white">{title}</h2>
            {description && <p className="mt-0.5 text-sm text-zinc-400">{description}</p>}
          </div>
          <button
            onClick={onClose}
            className="ml-4 rounded-md p-1 text-zinc-500 transition-colors hover:bg-white/[0.04] hover:text-white"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        <div className="px-6 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-white/[0.06] px-6 py-3">{footer}</div>}
      </div>
    </div>
  );
}
