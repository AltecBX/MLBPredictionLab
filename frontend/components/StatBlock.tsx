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
      <dt className="eyebrow">{label}</dt>
      <dd
        className="numeral-lg mt-1.5 text-[1.4375rem] leading-none sm:text-[1.625rem]"
        style={{ color, letterSpacing: "-0.04em" }}
      >
        {value}
      </dd>
      {sub ? <p className="t-micro mt-1.5 muted">{sub}</p> : null}
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
    <section className="card min-w-0 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="t-heading">{title}</h2>
          {description ? (
            <p className="t-small mt-1 max-w-prose muted">{description}</p>
          ) : null}
        </div>
        {actions}
      </div>
      <hr className="rule-soft my-3.5" />
      {children}
    </section>
  );
}
