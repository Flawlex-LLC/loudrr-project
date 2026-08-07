import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { ShellSwitch } from "@/components/shell/ShellSwitch";

// Vercel/Cloudflare-grade type: Geist Sans for everything (incl. tabular numbers); Geist Mono for code only.

export const metadata: Metadata = {
  metadataBase: new URL("https://loudrr.com"),
  title: {
    default: "Loudrr — the influence score for crypto X",
    template: "%s · Loudrr",
  },
  description:
    "Loudrr scores X accounts by the quality of who follows them — the influence score for crypto. Look up any handle, free.",
  openGraph: {
    title: "Loudrr — the influence score for crypto X",
    description: "Influence, measured by quality — not follower counts. Look up any X handle, free.",
    type: "website",
  },
  twitter: { card: "summary_large_image" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="min-h-screen">
        <Providers>
          <ShellSwitch>{children}</ShellSwitch>
        </Providers>
      </body>
    </html>
  );
}
