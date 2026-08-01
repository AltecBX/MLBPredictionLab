/**
 * The mark.
 *
 * A baseball's two seams reduced to their essential curves, inside a rounded
 * square. It has to survive being rendered at 22px on a phone header, so it is
 * two strokes and a circle — anything more becomes mud at that size — and it
 * inherits `currentColor` so it works on any surface in either theme without a
 * second asset.
 */
export function BrandMark({ size = 22 }: { size?: number }) {
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
