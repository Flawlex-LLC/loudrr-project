import Image from "next/image";
import Link from "next/link";

export function Logo({ className = "", priority = false }: { className?: string; priority?: boolean }) {
  return (
    <Link href="/" className={`inline-flex items-center ${className}`} aria-label="Loudrr home">
      <Image
        src="/loudrr-logo.png"
        alt="Loudrr"
        width={134}
        height={32}
        priority={priority}
        className="h-[26px] w-auto select-none"
      />
    </Link>
  );
}

// Standalone signal-ring mark (echoes the logo) — used as a decorative / loading motif.
export function SignalMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={className} aria-hidden>
      <defs>
        <clipPath id="mk-clip">
          <circle cx="32" cy="32" r="30" />
        </clipPath>
      </defs>
      <g clipPath="url(#mk-clip)">
        {Array.from({ length: 17 }).map((_, i) => (
          <rect key={i} x="0" y={i * 3.8} width="64" height="1.7" fill="#ff6a00" opacity={0.85 - i * 0.03} />
        ))}
      </g>
      <circle cx="32" cy="34" r="15" fill="#ff6a00" />
    </svg>
  );
}
