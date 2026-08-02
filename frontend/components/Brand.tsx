import { existsSync } from "node:fs";
import path from "node:path";

import Image from "next/image";

import { asset } from "@/lib/asset";

/**
 * The mark, in the header and the footer.
 *
 * It renders the real logo from `public/logo.png` when that file exists and
 * falls back to the drawn baseball below when it does not. The fallback is not
 * decoration: it is what keeps `npm run build` working in a checkout that has
 * not had the artwork added, and it means a missing asset degrades to a plain
 * mark rather than a broken-image icon in the corner of every page.
 *
 * The check runs once at module scope. Every page here is server-rendered, so
 * this is one stat per process rather than per request, and the answer cannot
 * change without a redeploy.
 */
const HAS_LOGO = existsSync(path.join(process.cwd(), "public", "logo.png"));

export function BrandMark({ size = 27 }: { size?: number }) {
  if (HAS_LOGO) {
    return (
      <Image
        src={asset("/logo.png")}
        alt=""
        width={size}
        height={size}
        // A detailed shield read at 22px in a header. Serving the full-resolution
        // file and letting the browser downscale is what keeps it legible at a
        // phone's pixel density.
        quality={90}
        priority
        aria-hidden="true"
        className="shrink-0 object-contain"
        style={{ width: size, height: size }}
      />
    );
  }
  return <DrawnMark size={size} />;
}

/**
 * The fallback mark.
 *
 * A baseball's two seams reduced to their essential curves, inside a rounded
 * square. It has to survive being rendered at 22px on a phone header, so it is
 * two strokes and a circle — anything more becomes mud at that size — and it
 * inherits the accent colour so it works on any surface in either theme without
 * a second asset.
 */
function DrawnMark({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <rect
        x="0.5"
        y="0.5"
        width="23"
        height="23"
        rx="6.5"
        fill="var(--accent)"
        fillOpacity="0.13"
        stroke="var(--accent)"
        strokeOpacity="0.34"
      />
      <circle
        cx="12"
        cy="12"
        r="6.25"
        stroke="var(--accent)"
        strokeWidth="1.5"
      />
      <path
        d="M7.9 7.1c1.5 1.3 2.35 2.9 2.35 4.9s-.85 3.6-2.35 4.9"
        stroke="var(--accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M16.1 7.1c-1.5 1.3-2.35 2.9-2.35 4.9s.85 3.6 2.35 4.9"
        stroke="var(--accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
