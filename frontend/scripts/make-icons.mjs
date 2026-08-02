/**
 * Every icon the product needs, from one source file.
 *
 *     node scripts/make-icons.mjs [path/to/logo.png]
 *
 * Defaults to `brand/logo-source.png`. Run it whenever the mark changes; the
 * outputs are committed, so this is a development script rather than a build
 * step — a deploy must never depend on regenerating artwork.
 *
 * Four outputs, and the differences between them are not cosmetic.
 *
 * **`icon-192` / `icon-512`** — the manifest's "any" icons. Transparent, padded
 * a little so the mark does not touch the edge of a tile.
 *
 * **`icon-maskable-512`** — Android crops a maskable icon to whatever shape the
 * launcher likes: circle, squircle, teardrop. The spec guarantees only the
 * central 80% is safe, so the mark is scaled to sit inside that and the rest is
 * filled. An unpadded maskable icon is how a logo ends up with its corners
 * shaved off on someone's home screen.
 *
 * **`apple-icon`** — iOS ignores transparency and composites onto black, which
 * turns a dark navy shield into a smudge. This one is flattened onto the app's
 * own background colour instead, and it is not rounded: iOS applies its own
 * corner radius and a pre-rounded source gets rounded twice.
 *
 * **`favicon.ico`** — 16/32/48 in one file. A browser tab renders this at 16px,
 * where a detailed logo becomes noise, so the source is padded less here to keep
 * the silhouette as large as the square allows.
 */

import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import sharp from "sharp";

const ROOT = path.resolve(import.meta.dirname, "..");
const SOURCE = path.resolve(ROOT, process.argv[2] ?? "brand/logo-source.png");
const OUT = path.resolve(ROOT, "public");

/** The manifest's background_color. iOS composites onto this rather than black. */
const APPLE_BACKGROUND = { r: 0x11, g: 0x15, b: 0x1f, alpha: 1 };

/** Android guarantees only the central 80% of a maskable icon survives cropping. */
const MASKABLE_SAFE_FRACTION = 0.8;

async function padded(size, { scale = 1, background = null }) {
  const inner = Math.round(size * scale);
  const margin = Math.round((size - inner) / 2);
  let image = sharp(SOURCE)
    .resize(inner, inner, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .extend({
      top: margin,
      bottom: size - inner - margin,
      left: margin,
      right: size - inner - margin,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    });
  if (background) image = image.flatten({ background });
  return image.png().toBuffer();
}

async function main() {
  if (!existsSync(SOURCE)) {
    console.error(
      `No source image at ${SOURCE}.\n\n` +
        `Put the logo there (a square PNG, ideally 1024x1024 with a\n` +
        `transparent background) and run this again. Nothing is generated from\n` +
        `a guess — an approximation of somebody's logo is worse than no logo.`,
    );
    process.exit(1);
  }

  const meta = await sharp(SOURCE).metadata();
  if (meta.width !== meta.height) {
    console.warn(
      `Warning: the source is ${meta.width}x${meta.height}, not square. It will` +
        ` be letterboxed rather than stretched, so the mark stays undistorted,` +
        ` but a square source gives a bigger icon.`,
    );
  }

  await mkdir(OUT, { recursive: true });

  const outputs = [
    // A hair of padding: enough that the mark does not collide with a tile edge.
    ["icon-192.png", await padded(192, { scale: 0.94 })],
    ["icon-512.png", await padded(512, { scale: 0.94 })],
    ["icon-maskable-512.png", await padded(512, { scale: MASKABLE_SAFE_FRACTION })],
    // Flattened, and nearly edge-to-edge: iOS adds its own rounding and its own
    // margin, so padding here would show up as a small icon in a big box.
    ["apple-icon.png", await padded(180, { scale: 1, background: APPLE_BACKGROUND })],
  ];

  for (const [name, buffer] of outputs) {
    await writeFile(path.join(OUT, name), buffer);
    console.log(`wrote public/${name}`);
  }

  // sharp cannot write .ico, and a 32px PNG named favicon.ico is a trick that
  // works in browsers and breaks in other readers. Next serves app/icon.png as
  // the tab icon anyway, so the honest move is to emit a real PNG and let the
  // framework do the linking.
  await writeFile(path.join(ROOT, "app", "icon.png"), await padded(48, { scale: 1 }));
  console.log("wrote app/icon.png (Next serves this as the tab icon)");
}

await main();
