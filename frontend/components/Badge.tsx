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
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[0.68rem] font-medium leading-none tracking-wide ${TONE_STYLE[tone]}`}
    >
      {children}
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
