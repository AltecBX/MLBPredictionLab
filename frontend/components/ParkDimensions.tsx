/**
 * The outfield, drawn to scale from the three wall distances we actually have.
 *
 * A number like 330′ means little on its own; the same number drawn against
 * the other two shows the park's shape at a glance — Fenway's short left,
 * Comerica's deep center. Everything here is derived from real recorded
 * dimensions: the wall passes through the three measured points, the foul
 * lines and infield are fixed geometry (90-foot bases), and nothing is
 * rendered when a distance is missing. No dimensions, no drawing — an
 * invented outline would be the placeholder this product bans.
 */
export function ParkDimensions({
  lf,
  cf,
  rf,
}: {
  lf: number;
  cf: number;
  rf: number;
}) {
  const max = Math.max(lf, cf, rf);
  const R = 88; // radius of the deepest wall point, in viewBox units
  const ox = 110;
  const oy = 118;

  /** Polar around home plate: 90° is dead center, 135° the left-field line. */
  const pt = (deg: number, dist: number): [number, number] => {
    const rad = (deg * Math.PI) / 180;
    const r = (dist / max) * R;
    return [ox + Math.cos(rad) * r, oy - Math.sin(rad) * r];
  };
  const p = (xy: [number, number]) => `${xy[0].toFixed(1)},${xy[1].toFixed(1)}`;

  const lfP = pt(135, lf);
  const cfP = pt(90, cf);
  const rfP = pt(45, rf);
  // Control points at the mid-angles, pushed slightly out so the wall bows
  // like a wall rather than kinking at center field.
  const c1 = pt(112.5, ((lf + cf) / 2) * 1.05);
  const c2 = pt(67.5, ((cf + rf) / 2) * 1.05);
  const wall = `M ${p(lfP)} Q ${p(c1)} ${p(cfP)} Q ${p(c2)} ${p(rfP)}`;

  // The infield is not data, it is geometry: 90-foot base paths, drawn at the
  // same scale so the outfield reads as the distance it actually is.
  const first = pt(45, 90);
  const second = pt(90, 127.28);
  const third = pt(135, 90);
  const diamond = `M ${ox},${oy} L ${p(first)} L ${p(second)} L ${p(third)} Z`;

  const label = (deg: number, dist: number, text: string) => {
    const [x, y] = pt(deg, dist + max * 0.19);
    return { x: x.toFixed(1), y: (y + 3.5).toFixed(1), text };
  };
  const labels = [
    label(135, lf, `LF ${lf}′`),
    label(90, cf, `CF ${cf}′`),
    label(45, rf, `RF ${rf}′`),
  ];

  return (
    <svg
      viewBox="0 0 220 126"
      className="w-full max-w-[300px]"
      role="img"
      aria-label={`Outfield wall drawn to scale: left field ${lf} feet, center field ${cf}, right field ${rf}`}
    >
      {/* Foul lines out to the wall corners. */}
      <path
        d={`M ${ox},${oy} L ${p(lfP)} M ${ox},${oy} L ${p(rfP)}`}
        stroke="var(--border-strong)"
        strokeWidth="1"
        fill="none"
      />
      <path
        d={diamond}
        stroke="var(--border-strong)"
        strokeWidth="1"
        fill="color-mix(in srgb, var(--accent) 7%, transparent)"
      />
      {/* The wall itself, with a soft under-glow so it reads as the subject. */}
      <path
        d={wall}
        stroke="color-mix(in srgb, var(--accent) 24%, transparent)"
        strokeWidth="5"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d={wall}
        stroke="var(--accent)"
        strokeWidth="1.8"
        strokeLinecap="round"
        fill="none"
      />
      {[lfP, cfP, rfP].map((xy, i) => (
        <circle key={i} cx={xy[0]} cy={xy[1]} r="2.1" fill="var(--accent)" />
      ))}
      {labels.map((l) => (
        <text
          key={l.text}
          x={l.x}
          y={l.y}
          textAnchor="middle"
          fontSize="10"
          fontWeight="600"
          fill="var(--text)"
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {l.text}
        </text>
      ))}
    </svg>
  );
}
