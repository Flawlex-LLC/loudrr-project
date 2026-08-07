import type { NextConfig } from "next";

// The backend serves routes at its root (e.g. /user/, /session/start/). The
// frontend calls /api/miniapp/* and /api/loud/* — these rewrites proxy those
// to the backend, stripping the /api/miniapp and /api/loud prefixes. Set
// BACKEND_ORIGIN in the environment (defaults to the local dev backend).
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

const nextConfig: NextConfig = {
  output: 'standalone', // Required for Docker deployment
  reactCompiler: true,
  // Allow external images for avatars
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'pbs.twimg.com' },
      { protocol: 'https', hostname: '*.twimg.com' },
    ],
  },
  async rewrites() {
    return [
      // Trailing slash is re-appended on the destination: the FastAPI routes
      // require it (/user/, /session/start/), but Next normalizes the incoming
      // path to no trailing slash. Without this the backend would 307-redirect
      // cross-origin and break CORS. (The /api/miniapp and /api/loud prefixes
      // are stripped — the backend serves these at its root.)
      { source: '/api/miniapp/:path*', destination: `${BACKEND_ORIGIN}/:path*/` },
      { source: '/api/loud/:path*', destination: `${BACKEND_ORIGIN}/:path*/` },
      // OAuth callback lives at the backend root path
      { source: '/api/auth/:path*', destination: `${BACKEND_ORIGIN}/api/auth/:path*/` },
      // Admin RBAC endpoints — backend mounts them under /api/admin so we
      // preserve the prefix (unlike miniapp/loud) and just re-append the
      // trailing slash. The Next.js admin pages call /api/admin/* same-origin.
      { source: '/api/admin/:path*', destination: `${BACKEND_ORIGIN}/api/admin/:path*/` },
    ];
  },
};

export default nextConfig;
