import type { NextConfig } from "next";
import path from "path";

// Pin the Turbopack workspace root. We resolve upward from web/ to the
// nearest directory containing node_modules/next/package.json — that's
// the actual Next.js install location. Falling back to process.cwd()
// (web/) fails when web/ doesn't have its own node_modules.
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
function findWorkspaceRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 10; i++) {
    const candidate = path.join(dir, "node_modules", "next", "package.json");
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      require("fs").accessSync(candidate);
      return dir;
    } catch {
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  return process.cwd();
}

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  turbopack: {
    root: findWorkspaceRoot(),
  },
};

export default nextConfig;
