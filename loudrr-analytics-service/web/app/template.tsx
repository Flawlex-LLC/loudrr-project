"use client";

import { motion, useReducedMotion } from "framer-motion";

// template.tsx re-mounts on every navigation, so this gives a smooth content fade-rise on each
// route change. The app-shell (sidebar + top bar) lives in layout.tsx and stays put — only the
// content transitions, which is what makes navigation feel buttery.
export default function Template({ children }: { children: React.ReactNode }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.34, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  );
}
