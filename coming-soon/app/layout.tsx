import type { Metadata, Viewport } from "next";
import { Plus_Jakarta_Sans, Syne } from "next/font/google";
import "./globals.css";

const plusJakarta = Plus_Jakarta_Sans({
  variable: "--font-plus-jakarta",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
});

const syne = Syne({
  variable: "--font-syne",
  subsets: ["latin"],
  weight: ["700", "800"],
});

export const metadata: Metadata = {
  title: "Loudrr — Coming Soon",
  description:
    "Loudrr is launching soon. A karma-based attention marketplace for creators who lead.",
  icons: {
    icon: [{ url: "/loudrr-icon.png", type: "image/png" }],
    apple: "/loudrr-icon.png",
  },
  openGraph: {
    title: "Loudrr — Coming Soon",
    description: "Stand out. Go Loudrr.",
    type: "website",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${plusJakarta.variable} ${syne.variable}`}
    >
      <body className="antialiased bg-[#0A0A0A] text-white">{children}</body>
    </html>
  );
}
