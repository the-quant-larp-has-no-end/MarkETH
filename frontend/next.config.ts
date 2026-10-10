import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The prod image runs the standalone server (frontend/Dockerfile).
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // The brand images are small pixel-art PNGs, served as the files they are.
  images: { unoptimized: true },
  // The rules of this project are in CLAUDE.md, which also says where the documentation
  // of this Next.js version is. Without this, `next dev` writes an AGENTS.md of its own
  // into the working tree whenever it is started by a coding agent.
  agentRules: false,
};

export default nextConfig;
