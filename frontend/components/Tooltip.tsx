"use client";

import { useId, useState, type ReactNode } from "react";

/**
 * Accessible tooltip: focusable trigger, described-by association, dismissible
 * with Escape. Used for explaining sample sizes and metric definitions.
 */
export function Tooltip({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-describedby={open ? id : undefined}
        aria-label="More information"
        className="inline-flex cursor-help items-center"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      >
        {children}
      </button>
      {open ? (
        <span
          role="tooltip"
          id={id}
          className="absolute bottom-full left-1/2 z-50 mb-1.5 w-64 -translate-x-1/2 rounded-md border border-[var(--border-strong)] bg-[var(--surface-raised)] p-2.5 text-xs leading-relaxed text-[var(--text)] shadow-lg"
        >
          {label}
        </span>
      ) : null}
    </span>
  );
}

export function InfoIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden
      className="size-3.5 opacity-55"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <circle cx="8" cy="8" r="6.5" />
      <path d="M8 7.2v4M8 4.8v.6" strokeLinecap="round" />
    </svg>
  );
}
