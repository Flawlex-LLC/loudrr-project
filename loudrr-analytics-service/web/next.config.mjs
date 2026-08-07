/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle for the Docker image (Coolify deploy): copies only the
  // traced runtime deps, so the final image is ~150MB instead of shipping node_modules.
  output: "standalone",
  images: {
    // X/Twitter avatars for leaderboard + profile cards (mock uses unavatar/X CDNs)
    remotePatterns: [
      { protocol: "https", hostname: "**.twimg.com" },
      { protocol: "https", hostname: "unavatar.io" },
      { protocol: "https", hostname: "**.x.com" },
    ],
  },
};

export default nextConfig;
