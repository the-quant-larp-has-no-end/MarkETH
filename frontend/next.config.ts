import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The prod image runs the standalone server (frontend/Dockerfile).
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // The brand images are small pixel-art PNGs, served as the files they are. A component
  // shows one from the address the build gives its import (components/brand/files.ts):
  // that address carries a hash of the file, and the server answers it as immutable by
  // itself. So no cache header is set here, and none is given to an address under
  // /brand/, which stays the same when its file changes.
  images: { unoptimized: true },
  // The rules of this project are in CLAUDE.md, which also says where the documentation
  // of this Next.js version is. Without this, `next dev` writes an AGENTS.md of its own
  // into the working tree whenever it is started by a coding agent.
  agentRules: false,
};

export default nextConfig;
