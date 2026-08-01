"use client";

import { useId, useRef, useState, type ReactNode } from "react";

/**
 * Accessible tooltip: focusable trigger, described-by association, dismissible
 * with Escape.
 *
 * Two things here exist for touch:
 *
 * 1. `onClick` toggles. Hover does not exist on a phone, and relying on the
 *    focus a tap happens to produce means the panel never closes again.
 * 2. The `after:` pseudo-element extends the hit area to 44pt without changing
 *    a single pixel of layout. The icon itself is 14px and sits inline inside
 *    running text — growing the element would push that text around; growing
 *    only what the browser hit-tests would not.
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
  // Which input opened it. A tap fires pointerenter *and* click, so hover and
  // tap handlers that both flip state cancel each other out and the panel never
  // appears on a phone. Branching on pointerType is what keeps them apart.
  const pointer = useRef<string>("mouse");

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-describedby={open ? id : undefined}
        aria-label="More information"
        className="relative inline-flex cursor-help items-center after:absolute after:-inset-[15px] after:content-[''] sm:after:hidden"
        onPointerEnter={(e) => {
          pointer.current = e.pointerType;
          if (e.pointerType === "mouse") setOpen(true);
        }}
        onPointerLeave={(e) => {
          if (e.pointerType === "mouse") setOpen(false);
        }}
        onClick={() => {
          if (pointer.current !== "mouse") setOpen((v) => !v);
        }}
        // Keyboard focus opens it; the focus a tap incidentally produces must
        // not, or it would race the click handler to the same flag.
        onFocus={(e) => {
          if (e.target.matches(":focus-visible")) setOpen(true);
        }}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      >
        {children}
      </button>
      {open ? (
        <span
          role="tooltip"
          id={id}
          // Phone: a sheet pinned above the tab bar. A 256px popover anchored
          // to a 14px icon near the screen edge either clips or drags the page
          // sideways, and there is no room to flip it. Pointer devices keep the
          // popover, where the anchor is the whole point.
          className="tip-sheet fixed inset-x-4 z-50 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-raised)] p-3 text-xs leading-relaxed text-[var(--text)] shadow-lg sm:absolute sm:inset-x-auto sm:left-1/2 sm:mb-1.5 sm:w-64 sm:-translate-x-1/2 sm:p-2.5"
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
      className="size-3.5 shrink-0 opacity-55"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <circle cx="8" cy="8" r="6.5" />
      <path d="M8 7.2v4M8 4.8v.6" strokeLinecap="round" />
    </svg>
  );
}
