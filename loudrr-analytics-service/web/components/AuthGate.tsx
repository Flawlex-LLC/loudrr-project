"use client";

import { AnimatePresence, motion } from "framer-motion";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { X } from "@phosphor-icons/react";
import { XBrandIcon } from "./icons/BrandIcons";
import { SignalMark } from "./Logo";

type GateCtx = {
  loggedIn: boolean;
  requireLogin: (reason?: string) => void;
  signOut: () => void;
};

const Ctx = createContext<GateCtx>({ loggedIn: false, requireLogin: () => {}, signOut: () => {} });
export const useAuthGate = () => useContext(Ctx);

const KEY = "loudrr.auth.v1";

export function AuthGateProvider({ children }: { children: React.ReactNode }) {
  const [loggedIn, setLoggedIn] = useState(false);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string | undefined>();

  useEffect(() => {
    setLoggedIn(typeof window !== "undefined" && localStorage.getItem(KEY) === "1");
  }, []);

  const requireLogin = useCallback(
    (r?: string) => {
      if (loggedIn) return;
      setReason(r);
      setOpen(true);
    },
    [loggedIn],
  );

  const completeLogin = () => {
    localStorage.setItem(KEY, "1");
    setLoggedIn(true);
    setOpen(false);
  };
  const signOut = () => {
    localStorage.removeItem(KEY);
    setLoggedIn(false);
  };

  return (
    <Ctx.Provider value={{ loggedIn, requireLogin, signOut }}>
      {children}
      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-[60] grid place-items-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="absolute inset-0 bg-ink-900/80 backdrop-blur-md" onClick={() => setOpen(false)} />
            <motion.div
              initial={{ y: 22, scale: 0.97, opacity: 0 }}
              animate={{ y: 0, scale: 1, opacity: 1 }}
              exit={{ y: 12, scale: 0.98, opacity: 0 }}
              transition={{ type: "spring", stiffness: 320, damping: 28 }}
              className="panel relative w-full max-w-md p-7"
            >
              <button
                onClick={() => setOpen(false)}
                className="absolute right-4 top-4 text-bone-500 transition-colors hover:text-bone-100"
                aria-label="Close"
              >
                <X size={18} />
              </button>

              <SignalMark size={34} />
              <h3 className="mt-4 font-display text-2xl font-bold tracking-tightest">
                {reason ? reason : "Sign in to continue"}
              </h3>
              <p className="mt-1.5 text-sm text-bone-500">
                Free account. Unlocks unlimited profile lookups, your API key, and saved watchlists.
              </p>

              <div className="mt-6 space-y-2.5">
                <button onClick={completeLogin} className="btn-flare w-full !py-3">
                  <XBrandIcon size={16} /> Continue with X
                </button>
                <button onClick={completeLogin} className="btn-ghost w-full !py-3">
                  Continue with email
                </button>
              </div>
              <p className="mt-4 text-center font-mono text-[10px] uppercase tracking-[0.2em] text-bone-600">
                No card · free forever tier
              </p>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </Ctx.Provider>
  );
}
