"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle, PaperPlaneRight, X } from "@phosphor-icons/react";
import { useState } from "react";
import { SignalMark } from "./Logo";

// morfi-style "Join waitlist" orange pill + a focused capture modal.
export function WaitlistButton({ className = "", label = "Join Creator Community" }: { className?: string; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} className={`btn-waitlist ${className}`}>
        {label}
      </button>
      <WaitlistModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}

export function WaitlistModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.includes("@")) return;
    setDone(true);
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[60] grid place-items-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <div className="absolute inset-0 bg-ink-900/80 backdrop-blur-md" onClick={onClose} />
          <motion.div
            initial={{ y: 22, scale: 0.97, opacity: 0 }}
            animate={{ y: 0, scale: 1, opacity: 1 }}
            exit={{ y: 12, scale: 0.98, opacity: 0 }}
            transition={{ type: "spring", stiffness: 320, damping: 28 }}
            className="panel relative w-full max-w-md p-7"
          >
            <button onClick={onClose} className="absolute right-4 top-4 text-bone-500 transition-colors hover:text-bone-100" aria-label="Close">
              <X size={18} />
            </button>

            {done ? (
              <div className="py-6 text-center">
                <CheckCircle size={48} weight="fill" className="mx-auto text-up" />
                <h3 className="mt-4 font-display text-2xl font-bold tracking-tightest">You&apos;re on the list</h3>
                <p className="mt-1.5 text-sm text-bone-500">
                  We&apos;ll ping <span className="font-mono text-bone-300">{email}</span> when the next vertical and the
                  pro API tiers open up.
                </p>
                <button onClick={onClose} className="btn-ghost mt-6">
                  Done
                </button>
              </div>
            ) : (
              <>
                <SignalMark size={34} />
                <h3 className="mt-4 font-display text-2xl font-bold tracking-tightest">Join the creator community</h3>
                <p className="mt-1.5 text-sm text-bone-500">
                  Early access, score alerts, and creator perks — new verticals (AI, stocks) and higher API limits
                  before they ship.
                </p>
                <form onSubmit={submit} className="mt-6 flex gap-2">
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@protocol.xyz"
                    className="min-w-0 flex-1 rounded-xl border border-white/10 bg-ink-800/70 px-4 py-3 font-mono text-sm text-bone-100 placeholder:text-bone-600 focus:border-flare/60 focus:outline-none"
                  />
                  <button type="submit" className="btn-flare shrink-0">
                    <PaperPlaneRight size={16} weight="fill" />
                  </button>
                </form>
                <p className="mt-4 text-center font-mono text-[10px] uppercase tracking-[0.2em] text-bone-600">
                  No spam · unsubscribe anytime
                </p>
              </>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
