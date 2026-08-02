import type { NextConfig } from "next";

/**
 * Three build modes, because the same code is served three different ways.
 *
 * **Static export** is how the site is actually published. Every page becomes a
 * file, GitHub Pages serves it from a CDN, and nothing in the read path can be
 * asleep. That is not a preference — Render's free plan sleeps a service after
 * fifteen minutes idle, and GitHub throttles scheduled workflows to roughly
 * hourly no matter what the cron says, so a keep-warm ping cannot outrun a
 * fifteen-minute timer. Measured on this repository, a ten-minute schedule
 * produced runs at 14:28, 15:40, 16:39, 17:44 and 18:44 — hourly. A reader
 * arriving in any of those gaps met a 502.
 *
 * The data does not need a server either. One slate a day, written by a
 * scheduled job, every number tracing to a stored record — that is publishing,
 * not serving.
 *
 * **Standalone** remains for the container image, and **default** for local
 * development and the Playwright suite, which run `next dev`/`next start`.
 */
const isExport = process.env.NEXT_STATIC_EXPORT === "1";

// A project Pages site is served under /<repo>/, so every link and asset needs
// the prefix. Empty for a custom domain or a user/org Pages site, which is why
// it comes from the environment rather than being hard-coded.
const basePath = process.env.NEXT_BASE_PATH ?? "";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: true },

  ...(process.env.NEXT_BUILD_STANDALONE === "1"
    ? { output: "standalone" as const }
    : {}),

  ...(isExport
    ? {
        output: "export" as const,
        // Pages resolves /d/2026-08-02/ to that directory's index.html. Without
        // this the export emits /d/2026-08-02.html, which Pages will not serve
        // at the link the app generates.
        trailingSlash: true,
        ...(basePath ? { basePath, assetPrefix: basePath } : {}),
        // The optimizer is a server. There is not one.
        images: { unoptimized: true },
      }
    : {
        // `headers()` is a server feature and Next refuses to export with one
        // configured. On Pages these are set by the host; the meta equivalents
        // that matter are in the document head.
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
      }),
};

export default config;
