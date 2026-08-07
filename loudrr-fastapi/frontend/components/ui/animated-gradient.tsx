"use client";

import { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";

interface AnimatedGradientTextProps {
  children: ReactNode;
  className?: string;
}

export default function AnimatedGradientText({
  children,
  className,
}: AnimatedGradientTextProps) {
  return (
    <div
      className={cn(
        "group relative mx-auto flex max-w-fit flex-row items-center justify-center rounded-2xl bg-white/10 px-4 py-1.5 text-sm font-medium shadow-[inset_0_-8px_10px_#ffffff1f] backdrop-blur-sm transition-all duration-300 ease-out hover:bg-white/20 hover:shadow-[inset_0_-8px_10px_#ffffff3f]",
        className,
      )}
    >
      <div
        className={cn(
          "absolute inset-0 rounded-2xl bg-gradient-to-r from-[#f95400] via-[#ff8c42] to-[#f95400] bg-[length:200%_100%] opacity-0 blur-sm transition-opacity duration-300 group-hover:opacity-100",
        )}
        style={
          {
            animation: "gradient 8s linear infinite",
          } as CSSProperties
        }
      />
      <div className="relative z-10 flex items-center gap-2">
        {children}
      </div>
    </div>
  );
}
