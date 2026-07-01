import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Session/index JSON is read from disk at request time; make sure it is
  // shipped with the serverless bundle on Vercel.
  outputFileTracingIncludes: {
    "/**": ["./data/index.json", "./data/sessions/**"],
  },
};

export default nextConfig;
