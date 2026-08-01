import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: true },

  // Container builds only. Standalone output traces the exact files the server
  // needs and emits its own minimal node_modules, which is the difference
  // between a 1.8 GB image and a small one — and it guarantees the test
  // toolchain cannot end up in a public-facing container, which pruning
  // devDependencies after the fact did not reliably do.
  //
  // Conditional because `next start` refuses to run against standalone output,
  // and that is what local development and the Playwright suite use.
  ...(process.env.NEXT_BUILD_STANDALONE === "1"
    ? { output: "standalone" as const }
    : {}),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
        ],
      },
    ];
  },
};

export default config;
