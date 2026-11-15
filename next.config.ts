import type { NextConfig } from "next";

const apiOrigin = process.env.FINOPS_API_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  devIndicators: false,
  experimental: { proxyTimeout: 540_000 },
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/:path*` }];
  },
};

export default config;
