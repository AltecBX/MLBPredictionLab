import type { ReactNode } from "react";

export function StatBlock({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "home" | "away" | "accent";
}) {
  const color =
    tone === "home"
      ? "var(--home)"
      : tone === "away"
        ? "var(--away)"
        : tone === "accent"
          ? "var(--accent)"
          : undefined;
  return (
    <div className="min-w-0">
      <dt className="text-[0.7rem] uppercase tracking-wide subtle">{label}</dt>
      <dd className="tnum mt-1 text-base font-semibold sm:text-lg" style={{ color }}>
        {value}
      </dd>
      {sub ? <p className="mt-0.5 text-xs muted">{sub}</p> : null}
    </div>
  );
}

export function Section({
  title,
  description,
  children,
  actions,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    // min-w-0 twice, deliberately: the section is a grid item and the header
    // text block is a flex item, and both default to min-width:auto. Without
    // them a single long heading sets the grid track's minimum and pushes the
    // whole page sideways on a phone instead of wrapping.
    <section className="surface min-w-0 p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          {description ? (
            <p className="mt-0.5 max-w-prose text-xs leading-relaxed muted">
              {description}
            </p>
          ) : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
