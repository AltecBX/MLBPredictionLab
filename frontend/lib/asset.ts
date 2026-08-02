/**
 * Prefix a public asset with the deployment's base path.
 *
 * A project Pages site is served from `/<repo>/`, and Next only rewrites some
 * URLs for you — metadata routes like the manifest get the prefix, but a plain
 * `src="/logo.png"`, an `icons` entry in the manifest body, and an
 * `apple-touch-icon` href do not. Measured on a real export with the base path
 * set: the manifest link came out `/MLBPredictionLab/manifest.webmanifest` and
 * the logo came out `/logo.png`, which resolves to the domain root and 404s.
 *
 * Empty in every other mode, so local development and the container image are
 * unaffected.
 */
const BASE = process.env.NEXT_BASE_PATH ?? "";

export function asset(path: string): string {
  return `${BASE}${path}`;
}
