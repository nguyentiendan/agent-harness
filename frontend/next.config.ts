import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: the dashboard is a fully client-side app served by Nginx.
  output: "export",
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
