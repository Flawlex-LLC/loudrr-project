/**
 * /x-login#u=<X authorize URL> — a hop that keeps X sign-in in the browser.
 *
 * On phones a tapped x.com link is handed to the X app, and the X app can't
 * run third-party sign-in ("flow name login is currently inaccessible"). A
 * script redirect isn't a tap, so the browser keeps it. The script below runs
 * while the page loads, without waiting for the app's JavaScript, and only
 * ever forwards to X's own OAuth authorize page. The link rides in the #
 * fragment, which browsers never send to our server.
 */
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Opening X…',
  robots: { index: false, follow: false },
};

const HOP = `(function () {
  var u = '';
  try { u = decodeURIComponent((location.hash.match(/[#&]u=([^&]+)/) || [])[1] || ''); } catch (e) {}
  var ok = false;
  try {
    var p = new URL(u);
    ok = p.protocol === 'https:' && (p.hostname === 'x.com' || p.hostname === 'twitter.com') &&
      p.pathname === '/i/oauth2/authorize';
  } catch (e) {}
  if (ok) { location.replace(u); }
  else { document.documentElement.setAttribute('data-x-login', 'invalid'); }
})();`;

const STYLE = `
  [data-x-login="invalid"] .x-login-going { display: none; }
  .x-login-invalid { display: none; }
  [data-x-login="invalid"] .x-login-invalid { display: block; }
`;

export default function XLoginHop() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#0A0A0A] p-6 text-center text-white">
      <style dangerouslySetInnerHTML={{ __html: STYLE }} />
      <script dangerouslySetInnerHTML={{ __html: HOP }} />
      <div className="x-login-going max-w-sm">
        <p className="text-lg font-semibold">Opening X…</p>
        <p className="mt-2 text-sm text-gray-400">Sign in to X if it asks, then approve Loudrr.</p>
      </div>
      <div className="x-login-invalid max-w-sm">
        <p className="text-lg font-semibold">This sign-in link isn&apos;t valid</p>
        <p className="mt-2 text-sm text-gray-400">Go back to Loudrr in Telegram and tap Connect X again.</p>
      </div>
    </main>
  );
}
