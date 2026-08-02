import type { MetadataRoute } from "next";

/**
 * Installable web app.
 *
 * `display: standalone` is what makes "Add to Home Screen" on iOS open without
 * Safari's chrome, which is worth roughly 120px of vertical space on a phone —
 * the difference between seeing two game cards and seeing one.
 */
// A manifest is a file, not a request. Static export needs that said
// explicitly for a route handler, or the build refuses it.
export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Jerry MLB Prediction Lab",
    short_name: "Jerry MLB",
    description:
      "Calibrated MLB win probabilities with the reasoning, the sample sizes and the measured historical reliability behind every number.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait-primary",
    background_color: "#f6f7f9",
    theme_color: "#11151f",
    categories: ["sports", "utilities"],
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    shortcuts: [
      { name: "Today's games", short_name: "Games", url: "/" },
      { name: "Backtest report", short_name: "Backtest", url: "/backtest" },
      { name: "Source health", short_name: "Health", url: "/diagnostics" },
    ],
  };
}
