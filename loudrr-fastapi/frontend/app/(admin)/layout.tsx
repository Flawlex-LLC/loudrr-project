import type { Metadata, Viewport } from "next";
import { plusJakarta, syne } from "../fonts";
import "../globals.css";

// Root layout for the admin website (admin.loudrr.com). It is a normal
// website, not the Telegram mini-app, so it loads neither Telegram's
// mini-app script nor Tag Manager. The (site) layout makes the page wait for
// telegram-web-app.js before it becomes interactive; when telegram.org is
// slow that is a blank screen, and the admin panel doesn't need the script.
// Sign-in loads Telegram's login widget on its own page only.

export const metadata: Metadata = {
  title: { default: "Loudrr admin", template: "%s · Loudrr admin" },
  robots: { index: false, follow: false },
  icons: {
    icon: [{ url: "/loudrr-icon.png", type: "image/png" }],
    apple: "/loudrr-icon.png",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "dark",
  themeColor: "#0A0A0A",
};

export default function AdminRootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${plusJakarta.variable} ${syne.variable}`}>
      <body className="antialiased bg-[#0A0A0A] text-white">{children}</body>
    </html>
  );
}
