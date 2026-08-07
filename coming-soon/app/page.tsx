"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import dynamic from "next/dynamic";

const AudioWaveGL = dynamic(() => import("./components/AudioWaveGL"), {
  ssr: false,
});

export default function LandingPage() {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <>
      <main className="h-screen h-[100dvh] w-full bg-[#0a0a0a] relative overflow-hidden">
        {/* WebGL Background - Full screen behind everything */}
      <div className="fixed inset-0 pointer-events-none">
        <AudioWaveGL />
      </div>

      {/* Gradient overlay for depth */}
      <div className="fixed inset-0 pointer-events-none bg-gradient-to-t from-[#0a0a0a] via-transparent to-[#0a0a0a]/80" />

      {/* Grain texture overlay - subtle soft grain */}
      <div
        className="fixed inset-0 pointer-events-none opacity-[0.12] mix-blend-soft-light"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.5' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
          backgroundRepeat: 'repeat',
          backgroundSize: '256px 256px',
        }}
      />
      {/* Second grain layer for subtle depth */}
      <div
        className="fixed inset-0 pointer-events-none opacity-[0.08] mix-blend-overlay"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise2'%3E%3CfeTurbulence type='turbulence' baseFrequency='0.6' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise2)'/%3E%3C/svg%3E")`,
          backgroundRepeat: 'repeat',
          backgroundSize: '200px 200px',
        }}
      />

      {/* Vignette effect */}
      <div
        className="fixed inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse at center, transparent 0%, transparent 40%, rgba(0,0,0,0.4) 100%)',
        }}
      />

      {/* Content wrapper */}
      <div className="relative z-10 h-screen h-[100dvh] flex flex-col overflow-hidden">
        {/* Header */}
        <header
          className={`
            flex items-center justify-between flex-shrink-0
            transition-all duration-1000 ease-out
            ${mounted ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-4"}
          `}
          style={{
            paddingLeft: 'clamp(1.25rem, 5vw, 7rem)',
            paddingRight: 'clamp(1.25rem, 5vw, 7rem)',
            paddingTop: 'clamp(1rem, 3vh, 3rem)',
            paddingBottom: 'clamp(0.5rem, 1.5vh, 1.5rem)'
          }}
        >
          <div className="flex items-center gap-2 group cursor-pointer relative">
            <div className="relative" style={{ width: 'clamp(32px, 5vw, 44px)', height: 'clamp(32px, 5vw, 44px)' }}>
              <Image
                src="/loudrr-icon.png"
                alt="Loudrr"
                fill
                priority
                className="transition-opacity duration-500 object-contain"
              />
              <div className="absolute inset-0 bg-[#f95400] rounded-full blur-xl opacity-30 group-hover:opacity-50 transition-opacity duration-500" />
            </div>
            <span className="font-syne font-bold text-[#f95400] tracking-tight" style={{ fontSize: 'clamp(1.25rem, 3vw, 1.5rem)' }}>
              Loudrr
            </span>

          </div>

          {/* Social buttons */}
          <div className="flex items-center gap-2">
            {/* X/Twitter button */}
            <a
              href="https://x.com/loudrrHQ"
              target="_blank"
              rel="noopener noreferrer"
              className="text-white/70 hover:text-white rounded-xl transition-all duration-300 border border-white/10 hover:border-white/20 bg-white/[0.02] hover:bg-white/[0.05] active:scale-[0.98] focus:outline-none focus:ring-0 flex items-center justify-center"
              style={{
                width: 'clamp(36px, 5vw, 44px)',
                height: 'clamp(36px, 5vw, 44px)',
              }}
            >
              <svg className="w-5 h-5 flex-shrink-0" viewBox="0 0 24 24" fill="currentColor">
                <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
              </svg>
            </a>

            {/* Contact button */}
            <a
              href="https://t.me/ace_flawlex"
              target="_blank"
              rel="noopener noreferrer"
              className="text-white/70 hover:text-white font-syne font-medium rounded-xl transition-all duration-300 border border-white/10 hover:border-white/20 bg-white/[0.02] hover:bg-white/[0.05] active:scale-[0.98] whitespace-nowrap focus:outline-none focus:ring-0 flex items-center justify-center gap-2"
              style={{
                fontSize: 'clamp(11px, 1.8vw, 14px)',
                height: 'clamp(36px, 5vw, 44px)',
                paddingLeft: 'clamp(10px, 1.5vw, 12px)',
                paddingRight: 'clamp(14px, 2vw, 18px)',
              }}
            >
              <svg className="w-5 h-5 flex-shrink-0" viewBox="0 0 24 24" fill="currentColor">
                <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/>
              </svg>
              <span>Contact</span>
            </a>
          </div>
        </header>

        {/* Main content */}
        <div
          className="flex-1 flex flex-col justify-center min-h-0"
          style={{
            paddingLeft: 'clamp(1.25rem, 5vw, 7rem)',
            paddingRight: 'clamp(1.25rem, 5vw, 7rem)',
            paddingTop: 'clamp(1rem, 3vh, 3rem)',
            paddingBottom: 'clamp(1rem, 3vh, 3rem)'
          }}
        >
          {/* Hero text */}
          <div className="max-w-4xl">
            <div
              className={`
                font-syne font-semibold uppercase tracking-[0.28em]
                text-[#f95400]/90
                transition-all duration-1000 ease-out delay-150
                ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"}
              `}
              style={{
                fontSize: 'clamp(16px, 2vw, 20px)',
                marginBottom: 'clamp(0.75rem, 2vh, 1.25rem)',
                paddingLeft: 'clamp(0.375rem, 1vw, 0.75rem)',
              }}
            >
              Coming soon
            </div>
            <h1
              className={`
                font-syne font-bold text-white
                leading-[0.85] tracking-[-0.02em]
                transition-all duration-1000 ease-out delay-200
                ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"}
              `}
              style={{ fontSize: 'clamp(3.25rem, 9vw, 6rem)' }}
            >
              <span className="whitespace-nowrap">Stand out</span>
              <br />
              <span className="text-[#f95400] inline-block cursor-default whitespace-nowrap">
                Go Loudrr
              </span>
            </h1>
            <p
              className={`
                text-white/40 font-medium
                transition-all duration-1000 ease-out delay-300
                ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"}
              `}
              style={{
                fontSize: 'clamp(1rem, 2.5vw, 1.5rem)',
                marginTop: 'clamp(0.25rem, 0.5vw, 0.5rem)',
                paddingLeft: 'clamp(0.375rem, 1vw, 0.75rem)',
              }}
            >
              For creators who lead.
            </p>
          </div>

        </div>

        {/* Footer */}
        <footer
          className={`
            flex-shrink-0
            flex items-center justify-between
            transition-all duration-1000 ease-out delay-700
            ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"}
          `}
          style={{
            paddingLeft: 'clamp(1.25rem, 5vw, 7rem)',
            paddingRight: 'clamp(1.25rem, 5vw, 7rem)',
            paddingTop: 'clamp(1rem, 2vh, 2rem)',
            paddingBottom: 'clamp(1rem, 3vh, 2.5rem)'
          }}
        >
          <p className="text-white/20" style={{ fontSize: 'clamp(11px, 1.5vw, 14px)' }}>
            built to make communities louder, together.
          </p>

          <span className="text-white/15" style={{ fontSize: 'clamp(10px, 1.3vw, 12px)' }}>© 2026 Loudrr</span>
        </footer>
      </div>
    </main>
    </>
  );
}
