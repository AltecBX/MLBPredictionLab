"use client";

import { useEffect, useRef } from "react";

export interface TabDef {
  key: string;
  label: string;
  /** Shorter wording used on a phone, where the strip is swiped, not scanned. */
  shortLabel?: string;
}

/**
 * The tab strip. Selection is the caller's — see `TabPanels`, which owns the
 * active tab and keeps it in the URL.
 *
 * Buttons rather than links: a static export serves one file per game, so a
 * `?tab=` link would navigate to the same page it is already on. The URL is
 * still updated, so deep links and the back button behave as they did.
 *
 * The client boundary also buys one thing it always did: after switching tab
 * the strip can be scrolled such that the tab you just tapped is off-screen on
 * a phone. This scrolls it back into view.
 */
export function Tabs({
  tabs,
  active,
  basePath,
  onSelect,
}: {
  tabs: TabDef[];
  active: string;
  basePath: string;
  onSelect: (key: string) => void;
}) {
  const activeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [active]);

  return (
    <nav
      aria-label="Game sections"
      // Sticky directly under the header: ten tabs on a phone means the strip
      // is the primary way around this page, and scrolling a long panel should
      // not strand it at the top.
      className="scroll-x no-bar snap-x-strip sticky z-20 -mx-4 border-b px-4 sm:-mx-6 sm:px-6"
      style={{
        top: "calc(var(--header-h) - 1px)",
        borderColor: "var(--border)",
        background: "color-mix(in srgb, var(--surface-sunken) 82%, transparent)",
        backdropFilter: "blur(14px) saturate(1.6)",
        WebkitBackdropFilter: "blur(14px) saturate(1.6)",
      }}
    >
      <ul className="flex min-w-max items-center gap-0.5">
        {tabs.map((tab) => {
          const isActive = tab.key === active;
          return (
            <li key={tab.key}>
              <button
                type="button"
                ref={isActive ? activeRef : undefined}
                onClick={() => onSelect(tab.key)}
                aria-current={isActive ? "page" : undefined}
                className={`tap t-small relative whitespace-nowrap px-3 transition-colors ${
                  isActive ? "" : "muted hover:text-[var(--text)]"
                }`}
                style={
                  isActive ? { color: "var(--accent)", fontWeight: 600 } : undefined
                }
              >
                <span className="sm:hidden">{tab.shortLabel ?? tab.label}</span>
                <span className="hidden sm:inline">{tab.label}</span>
                {/* The rule is inset from the label rather than running its full
                    width, and rounded. A full-bleed underline butting against
                    the neighbouring tab reads as a border; a short one reads as
                    a marker. */}
                {isActive ? (
                  <span
                    aria-hidden
                    className="absolute inset-x-2 bottom-0 h-[2px] rounded-t-full"
                    style={{ background: "var(--accent)" }}
                  />
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
