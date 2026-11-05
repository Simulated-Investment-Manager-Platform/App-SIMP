import type { NextConfig } from "next";

const api = process.env.SIMP_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Same-origin API: the browser talks to /api/*, Next proxies to FastAPI (cookies + SSE work).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default config;
