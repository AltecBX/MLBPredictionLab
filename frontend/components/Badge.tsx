import type { ReactNode } from "react";

type Tone = "neutral" | "home" | "away" | "warn" | "danger" | "accent" | "muted";

const TONE_STYLE: Record<Tone, string> = {
  neutral: "border-[var(--border-strong)] text-[var(--text-muted)]",
  muted: "border-transparent bg-[var(--surface-sunken)] text-[var(--text-subtle)]",
  home: "border-transparent text-[var(--home)] bg-[color-mix(in_srgb,var(--home)_12%,transparent)]",
  away: "border-transparent text-[var(--away)] bg-[color-mix(in_srgb,var(--away)_12%,transparent)]",
  accent:
    "border-transparent text-[var(--accent)] bg-[color-mix(in_srgb,var(--accent)_12%,transparent)]",
  warn: "border-transparent text-[var(--color-warn-500)] bg-[color-mix(in_srgb,var(--color-warn-500)_14%,transparent)] dark:text-[var(--color-warn-400)]",
  danger:
    "border-transparent text-[var(--color-danger-500)] bg-[color-mix(in_srgb,var(--color-danger-500)_14%,transparent)] dark:text-[var(--color-danger-400)]",
};

export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`t-micro inline-flex items-center gap-1 rounded-full border px-2 py-[0.1875rem] leading-none ${TONE_STYLE[tone]}`}
      style={{ fontWeight: 580, letterSpacing: "0.015em" }}
    >
      {children}
    </span>
  );
}

/**
 * A team's abbreviation set as a fixed-width chip.
 *
 * Three letters in a box, monospaced and centred, so a column of them aligns
 * exactly and the eye can find a club without reading. It is the closest thing
 * to a crest this product has, and unlike a crest it needs no asset, no licence
 * and no network request.
 */
export function TeamTag({
  abbreviation,
  emphasis = false,
  tone,
}: {
  abbreviation: string;
  emphasis?: boolean;
  tone?: string;
}) {
  return (
    <span
      aria-hidden
      className="t-micro inline-flex h-6 w-[2.875rem] shrink-0 items-center justify-center rounded-[var(--radius-sm)] border font-mono"
      style={{
        letterSpacing: "0.04em",
        fontWeight: 620,
        color: emphasis && tone ? tone : "var(--text-muted)",
        borderColor:
          emphasis && tone
            ? `color-mix(in srgb, ${tone} 34%, transparent)`
            : "var(--border)",
        background:
          emphasis && tone
            ? `color-mix(in srgb, ${tone} 10%, transparent)`
            : "var(--surface-inset)",
      }}
    >
      {abbreviation}
    </span>
  );
}

export function Dot({ tone }: { tone: "ok" | "warn" | "bad" | "off" }) {
  const color =
    tone === "ok"
      ? "var(--home)"
      : tone === "warn"
        ? "var(--color-warn-500)"
        : tone === "bad"
          ? "var(--color-danger-500)"
          : "var(--text-subtle)";
  return (
    <span
      aria-hidden
      className="inline-block size-1.5 shrink-0 rounded-full"
      style={{ background: color }}
    />
  );
}
