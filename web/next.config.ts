import type { NextConfig } from "next";

// Pin the Turbopack workspace root to the web/ directory (process.cwd()
// at config-load time, since dev/build are always run from web/).
//
// Without this, Next.js scans upward for lockfiles and may pick a parent
// directory (e.g. ~/package-lock.json from another project) as the
// workspace root — which makes Turbopack sweep up stray middleware.ts /
// config files living in that parent and produces a build error:
//   "Middleware is missing expected function export name"  → ./src/middleware.ts
// even though no such file exists in this repo.
//
// Anchoring root here makes the repo immune to that class of bug for
// anyone who clones it, regardless of stray lockfiles in their home.
const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  turbopack: {
    root: process.cwd(),
  },
};

export default nextConfig;
