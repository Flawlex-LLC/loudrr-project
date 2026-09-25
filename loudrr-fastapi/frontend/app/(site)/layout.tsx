import type { Metadata, Viewport } from "next";
import Script from "next/script";
import { plusJakarta, syne } from "../fonts";
import "../globals.css";

// Root layout for the mini-app and the landing page. The admin website has
// its own root layout in app/(admin): it must not wait on telegram.org.

export const metadata: Metadata = {
  title: "Loudrr - Earn Karma by Engaging",
  description: "Loudrr is a karma-based attention marketplace. Earn karma by engaging with posts. Spend karma to get engagement on yours.",
  icons: {
    icon: [
      { url: "/loudrr-icon.png", type: "image/png" },
    ],
    apple: "/loudrr-icon.png",
  },
  openGraph: {
    title: "Loudrr - Earn Karma by Engaging",
    description: "Join the waitlist for Loudrr - a karma-based attention marketplace.",
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

const GTM_ID = "GTM-5N3W93HT";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${plusJakarta.variable} ${syne.variable}`}>
      <head>
        <Script
          src="https://telegram.org/js/telegram-web-app.js"
          strategy="beforeInteractive"
        />
        {/* Telegram opens the mini-app with the signed login (#tgWebAppData=…) in
            the URL hash — a credential. Tags read the page URL, so drop the hash
            before GTM loads (telegram-web-app.js has already parsed it into
            window.Telegram.WebApp, which is all the app reads), and never load
            GTM while Telegram still needs it. */}
        <Script
          id="gtm-script"
          strategy="afterInteractive"
          dangerouslySetInnerHTML={{
            __html: `
              (function(){
                if ((location.hash || '').indexOf('tgWebApp') !== -1) {
                  var tg = window.Telegram && window.Telegram.WebApp;
                  if (!tg || !tg.initData) return;
                  history.replaceState(history.state, '', location.pathname + location.search);
                }
                (function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
                new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
                j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
                'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
                })(window,document,'script','dataLayer','${GTM_ID}');
              })();
            `,
          }}
        />
      </head>
      <body className="antialiased bg-[#0A0A0A] text-white">
        <noscript>
          <iframe
            src={`https://www.googletagmanager.com/ns.html?id=${GTM_ID}`}
            height="0"
            width="0"
            style={{ display: "none", visibility: "hidden" }}
          />
        </noscript>
        {children}
      </body>
    </html>
  );
}
