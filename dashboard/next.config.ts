import type { NextConfig } from "next";

// Browser → /api/v1/* is handled by the server-side proxy route at
// src/app/api/v1/[...path]/route.ts, which injects BACKEND_API_KEY and
// forwards to BACKEND_URL. No rewrites needed — the key must never reach
// the browser.
const nextConfig: NextConfig = {
  experimental: {
    agentUpgrade: "security",
  },
};

export default nextConfig;
